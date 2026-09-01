import json
from pathlib import Path
import pytest
from aic.council import post_b4_watch_finalization_v01 as f
ROOT=Path('.aic-runtime')
def judge(): return json.loads((ROOT/'b4_post_research_reopen_current_judge_council_freeze_v0_3.json').read_text())
def test_watch_freeze_and_blocked_finalization_are_strict():
 d=f.verify_judge(judge()); assert [x['condition_id'] for x in d['what_would_change_decision']]==['NVDA_CONDITION_001','MSFT_CONDITION_001','META_CONDITION_001']; b=f.blocked(judge()); assert b['B5_HANDOFF_ELIGIBLE'] is False and b['missing_authority_fields']==['DECISION_DRAFT_B4_v0_4.created_at']
def test_tampered_watch_and_costs_fail_closed():
 p=judge();p['status']='BAD'
 with pytest.raises(Exception):f.verify_judge(p)
 c=f.provenance(judge());assert c['FINAL_VALID_B4_PRODUCTION_CYCLE_KNOWN_ACTUAL_COST_USD']=='3.089588' and c['TOTAL_PROJECT_SPEND_USD']=='NOT_COMPUTED_BY_THIS_ARTIFACT'
