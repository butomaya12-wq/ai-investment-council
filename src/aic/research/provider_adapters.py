from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aic.b2.models import EvidenceItem, ProviderReadReceipt
from aic.data.providers.alpaca_news import (
    ALPACA_NEWS_NORMALIZATION_VERSION,
    AlpacaNewsReadError,
    AlpacaNewsTransport,
    read_alpaca_news_window,
)
from aic.data.providers.sec_filings import (
    SEC_SECTION_NORMALIZATION_VERSION,
    SecFilingReadError,
    SecHttpTransport,
    read_sec_filing_sections,
)
from aic.domain.canonical import canonical_sha256

from .handoff import B2RealEventHandoff
from .retrieve import (
    RetrievalAction,
    RetrievalExecutionResult,
    RetrievalExecutionStatus,
    RetrievalProvider,
    RetrievalRequest,
)


class EventRetrievalAdapterError(ValueError):
    pass


def _failure_receipt(
    request: RetrievalRequest,
    *,
    started_at: datetime,
    error_code: str,
) -> ProviderReadReceipt:
    return ProviderReadReceipt(
        provider_read_receipt_id=f"B3_{request.candidate_id}_{request.need_id}_FAILED",
        provider=request.provider.value,
        endpoint_class=request.action.value,
        request_start=started_at,
        response_received_at=datetime.now(UTC),
        request_parameters_hash=canonical_sha256(request.parameters),
        pagination_complete=False,
        raw_payload_hash=canonical_sha256(
            {
                "provider": request.provider.value,
                "action": request.action.value,
                "status": "FAILED",
                "error_code": error_code,
            }
        ),
        record_count=0,
        http_status=None,
        error=error_code,
    )


def _safe_failure_result(
    request: RetrievalRequest,
    *,
    started_at: datetime,
    error_code: str,
) -> RetrievalExecutionResult:
    return RetrievalExecutionResult.build(
        request=request,
        receipt=_failure_receipt(
            request,
            started_at=started_at,
            error_code=error_code,
        ),
        evidence_items=(),
        computed_values=(),
        conflict_ids=(),
        status=RetrievalExecutionStatus.FAILED,
    )


@dataclass(frozen=True, slots=True)
class SecFilingRetrievalAdapter:
    handoff: B2RealEventHandoff
    user_agent: str
    transport: SecHttpTransport | None = None
    provider: RetrievalProvider = RetrievalProvider.SEC

    def execute(
        self,
        *,
        request: RetrievalRequest,
        research_cutoff: datetime,
    ) -> RetrievalExecutionResult:
        if request.provider is not self.provider:
            raise EventRetrievalAdapterError("SEC adapter received wrong provider request")
        if request.action is not RetrievalAction.GET_FILING_SECTIONS:
            raise EventRetrievalAdapterError("SEC adapter received unsupported action")
        candidate = self.handoff.candidate(request.candidate_id)
        accession = request.parameters.get("filing_accession")
        raw_sections = request.parameters.get("sections")
        if accession != candidate.sec_accession:
            raise EventRetrievalAdapterError(
                "SEC retrieval accession is not bound to frozen B2 event handoff"
            )
        if not isinstance(raw_sections, (tuple, list)) or not raw_sections:
            raise EventRetrievalAdapterError("SEC retrieval sections must be non-empty")
        sections = tuple(raw_sections)
        if any(type(section) is not str for section in sections):
            raise EventRetrievalAdapterError("SEC retrieval sections must be strings")
        if len(sections) > request.max_items:
            raise EventRetrievalAdapterError("SEC retrieval section count exceeds ResearchNeed max_items")

        started_at = datetime.now(UTC)
        try:
            read = read_sec_filing_sections(
                accession=candidate.sec_accession,
                source_uri=candidate.sec_source_uri,
                section_names=sections,
                research_cutoff=research_cutoff,
                user_agent=self.user_agent,
                transport=self.transport,
            )
        except SecFilingReadError:
            return _safe_failure_result(
                request,
                started_at=started_at,
                error_code="SEC_FILING_READ_FAILED",
            )

        receipt_id = f"B3_SEC_{request.candidate_id}_{request.need_id}_{read.raw_payload_hash[:16]}"
        receipt = ProviderReadReceipt(
            provider_read_receipt_id=receipt_id,
            provider=self.provider.value,
            endpoint_class=request.action.value,
            request_start=started_at,
            response_received_at=read.retrieved_at,
            request_parameters_hash=canonical_sha256(request.parameters),
            pagination_complete=True,
            raw_payload_hash=read.raw_payload_hash,
            record_count=len(read.sections),
            http_status=read.http_status,
            error=None,
        )
        evidence = tuple(
            EvidenceItem(
                evidence_id=f"B3_SEC_{request.candidate_id}_{request.need_id}_{index}",
                provider=self.provider.value,
                source_type="SEC_FILING_SECTION",
                source_uri=read.source_uri,
                request_parameters_ref=request.request_hash,
                entity_id=request.candidate_id,
                field_or_claim=section.section_name,
                raw_value_or_record_ref=f"{read.accession}#{section.section_name}",
                normalized_value=section.text,
                published_at=read.accepted_at,
                observed_at=None,
                retrieved_at=read.retrieved_at,
                as_of=read.accepted_at,
                freshness_rule_id="B3_SEC_RESEARCH_CUTOFF_V1",
                knowable_at_cutoff=read.accepted_at <= research_cutoff,
                authoritative_for=("B3_QUALITATIVE_SEC_RESEARCH",),
                conflict_group=None,
                provider_read_receipt_id=receipt_id,
                raw_content_hash=section.content_hash,
                normalization_version=SEC_SECTION_NORMALIZATION_VERSION,
            )
            for index, section in enumerate(read.sections, start=1)
        )
        return RetrievalExecutionResult.build(
            request=request,
            receipt=receipt,
            evidence_items=evidence,
            computed_values=(),
            conflict_ids=(),
            status=RetrievalExecutionStatus.COMPLETE,
        )


@dataclass(frozen=True, slots=True)
class AlpacaNewsRetrievalAdapter:
    api_key_id: str
    api_secret_key: str
    transport: AlpacaNewsTransport | None = None
    provider: RetrievalProvider = RetrievalProvider.ALPACA

    def execute(
        self,
        *,
        request: RetrievalRequest,
        research_cutoff: datetime,
    ) -> RetrievalExecutionResult:
        if request.provider is not self.provider:
            raise EventRetrievalAdapterError("Alpaca adapter received wrong provider request")
        if request.action is not RetrievalAction.GET_NEWS_WINDOW:
            raise EventRetrievalAdapterError(
                "event Alpaca adapter allows only bounded news retrieval"
            )
        candidate_id = request.parameters.get("candidate_id")
        window_start = request.parameters.get("window_start")
        window_end = request.parameters.get("window_end")
        if candidate_id != request.candidate_id:
            raise EventRetrievalAdapterError("Alpaca news candidate identity mismatch")
        if not isinstance(window_start, datetime) or not isinstance(window_end, datetime):
            raise EventRetrievalAdapterError("Alpaca news window parameters must be datetimes")

        started_at = datetime.now(UTC)
        try:
            read = read_alpaca_news_window(
                symbol=request.candidate_id,
                window_start=window_start,
                window_end=window_end,
                research_cutoff=research_cutoff,
                limit=request.max_items,
                api_key_id=self.api_key_id,
                api_secret_key=self.api_secret_key,
                transport=self.transport,
            )
        except AlpacaNewsReadError:
            return _safe_failure_result(
                request,
                started_at=started_at,
                error_code="ALPACA_NEWS_READ_FAILED",
            )

        receipt_id = f"B3_NEWS_{request.candidate_id}_{request.need_id}_{read.raw_payload_hash[:16]}"
        receipt = ProviderReadReceipt(
            provider_read_receipt_id=receipt_id,
            provider=self.provider.value,
            endpoint_class=request.action.value,
            request_start=started_at,
            response_received_at=read.retrieved_at,
            request_parameters_hash=canonical_sha256(request.parameters),
            pagination_complete=read.pagination_complete,
            raw_payload_hash=read.raw_payload_hash,
            record_count=len(read.articles),
            http_status=read.http_status,
            error=None,
        )
        evidence_items: list[EvidenceItem] = []
        for article in read.articles:
            normalized = json.dumps(
                {
                    "headline": article.headline,
                    "summary": article.summary,
                    "content": article.content,
                    "author": article.author,
                    "source": article.source,
                    "symbols": article.symbols,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            evidence_items.append(
                EvidenceItem(
                    evidence_id=f"B3_NEWS_{request.candidate_id}_{article.article_id}",
                    provider=self.provider.value,
                    source_type="ALPACA_NEWS",
                    source_uri=article.url,
                    request_parameters_ref=request.request_hash,
                    entity_id=request.candidate_id,
                    field_or_claim="CURRENT_NEWS_CONTEXT",
                    raw_value_or_record_ref=f"alpaca_news:{article.article_id}",
                    normalized_value=normalized,
                    published_at=article.created_at,
                    observed_at=article.updated_at,
                    retrieved_at=read.retrieved_at,
                    as_of=article.updated_at,
                    freshness_rule_id="B3_ALPACA_NEWS_CUTOFF_V1",
                    knowable_at_cutoff=(
                        article.created_at <= research_cutoff
                        and article.updated_at <= research_cutoff
                    ),
                    authoritative_for=("CURRENT_NEWS_CONTEXT",),
                    conflict_group=None,
                    provider_read_receipt_id=receipt_id,
                    raw_content_hash=article.content_hash,
                    normalization_version=ALPACA_NEWS_NORMALIZATION_VERSION,
                )
            )
        return RetrievalExecutionResult.build(
            request=request,
            receipt=receipt,
            evidence_items=tuple(evidence_items),
            computed_values=(),
            conflict_ids=(),
            status=(
                RetrievalExecutionStatus.COMPLETE
                if read.pagination_complete
                else RetrievalExecutionStatus.PARTIAL
            ),
        )
