(() => {
  "use strict";

  const STATE = {
    session: null,
    preflight: null,
    run: null,
    initialPreflight: null,
    initialApproval: null,
    preflightBusy: false,
    preflightError: null,
    approvalBusy: false,
    approvalError: null,
    looping: false,
  };

  const sleep = (ms) =>
    new Promise(
      (resolve) => setTimeout(resolve, ms)
    );

  async function json(url, options = {}) {
    const response =
      await fetch(
        url,
        {
          cache: "no-store",
          ...options,
        }
      );

    if (!response.ok) {
      const text =
        await response.text();

      throw new Error(
        `${response.status}: ${text}`
      );
    }

    return response.json();
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function shortHash(value) {
    const text = String(value ?? "");

    if (text.length <= 20) {
      return text;
    }

    return (
      text.slice(0, 9)
      + "…"
      + text.slice(-9)
    );
  }

  function style() {
    if (
      document.getElementById(
        "v62-analysis-style"
      )
    ) {
      return;
    }

    const node =
      document.createElement("style");

    node.id = "v62-analysis-style";

    node.textContent = `
      .v62-analysis {
        margin-top:12px;
        padding:12px;
        border:1px solid rgba(97,227,166,.23);
        border-radius:9px;
        background:
          linear-gradient(
            180deg,
            rgba(97,227,166,.055),
            rgba(10,15,19,.95)
          );
      }

      .v62-analysis-kicker {
        color:#6fd8c0;
        font-size:6px;
        font-weight:900;
        letter-spacing:.11em;
      }

      .v62-analysis h3 {
        margin:7px 0 4px;
        color:#eef3f5;
        font-size:14px;
      }

      .v62-analysis-copy {
        color:#7f8d97;
        font-size:7px;
        line-height:1.55;
      }

      .v62-call-plan {
        margin-top:9px;
        padding:8px;
        border:1px solid rgba(255,255,255,.07);
        border-radius:7px;
        color:#8b98a1;
        font-size:7px;
      }

      .v62-call-plan strong {
        color:#d9e2e6;
      }

      .v62-stages {
        display:grid;
        grid-template-columns:
          repeat(4, minmax(0,1fr));
        gap:6px;
        margin-top:10px;
      }

      .v62-stage {
        padding:8px;
        border:1px solid rgba(255,255,255,.08);
        border-radius:7px;
        background:#0b1115;
      }

      .v62-stage strong {
        display:block;
        color:#b8c3c9;
        font-size:7px;
      }

      .v62-stage span {
        display:block;
        margin-top:4px;
        color:#6d7c86;
        font-size:6px;
      }

      .v62-stage.complete {
        border-color:rgba(97,227,166,.26);
      }

      .v62-stage.complete span {
        color:#61e3a6;
      }

      .v62-button {
        width:100%;
        margin-top:10px;
        padding:10px 12px;
        border:1px solid rgba(97,227,166,.36);
        border-radius:7px;
        background:rgba(97,227,166,.10);
        color:#75e6b1;
        font-size:7px;
        font-weight:900;
        letter-spacing:.07em;
        cursor:pointer;
      }

      .v62-button:disabled {
        opacity:.55;
        cursor:default;
      }

      .v62-result {
        margin-top:9px;
        padding:9px;
        border:1px solid rgba(239,196,99,.24);
        border-radius:7px;
        background:rgba(239,196,99,.055);
      }

      .v62-result strong {
        color:#efc463;
        font-size:8px;
      }

      .v62-result span {
        display:block;
        margin-top:4px;
        color:#9b8a62;
        font-size:6px;
      }

      .v62-product-context {
        margin:10px 0 0;
        padding:8px 10px;
        border:1px solid rgba(97,227,166,.16);
        border-radius:7px;
        background:rgba(97,227,166,.035);
        color:#82909a;
        font-size:7px;
      }

      .v62-product-context strong {
        color:#77d9c4;
      }

      .v62-paid-preflight {
        margin-top:9px;
        padding:9px;
        border:1px solid rgba(111,216,192,.20);
        border-radius:7px;
        background:rgba(111,216,192,.035);
      }

      .v62-paid-preflight-head {
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:8px;
      }

      .v62-paid-preflight-head strong {
        color:#dce6e9;
        font-size:8px;
      }

      .v62-paid-preflight-cost {
        color:#75e6b1;
        font-size:11px;
        font-weight:900;
      }

      .v62-paid-preflight-meta {
        margin-top:6px;
        color:#7f8d97;
        font-size:6px;
        line-height:1.55;
      }

      .v62-preflight-error {
        margin-top:6px;
        color:#ef9a8a;
        font-size:6px;
      }

      .v62-approval-box {
        margin-top:8px;
        padding:8px;
        border:1px solid rgba(239,196,99,.22);
        border-radius:7px;
        background:rgba(239,196,99,.04);
      }

      .v62-approval-box.approved {
        border-color:rgba(97,227,166,.30);
        background:rgba(97,227,166,.055);
      }

      .v62-approval-title {
        color:#efc463;
        font-size:7px;
        font-weight:900;
        letter-spacing:.05em;
      }

      .v62-approval-box.approved
      .v62-approval-title {
        color:#75e6b1;
      }

      .v62-approval-copy {
        margin-top:5px;
        color:#7f8d97;
        font-size:6px;
        line-height:1.55;
      }

      @media(max-width:900px) {
        .v62-stages {
          grid-template-columns:
            repeat(2, minmax(0,1fr));
        }
      }
    `;

    document.head.appendChild(node);
  }

  function stageStatus(lane) {
    const row =
      STATE.run?.agents?.find(
        (item) => item.lane === lane
      );

    return row?.status || "PENDING";
  }

  function panelHtml() {
    const symbol =
      STATE.session?.symbol || "—";

    const plan =
      STATE.preflight
        ?.real_council_call_plan;

    const initial =
      STATE.initialPreflight;

    const hasInitialPreflight =
      initial?.available === true;

    const approval =
      STATE.initialApproval;

    const approved =
      approval?.available === true
      && approval?.owner_approval_granted === true
      && approval?.preflight_id === initial?.preflight_id;

    const running =
      STATE.run?.status === "RUNNING";

    const completed =
      STATE.run?.status === "COMPLETED";

    const lanes = [
      ["BULL", "Bull"],
      ["BEAR", "Bear"],
      ["RED_TEAM", "Red Team"],
      ["JUDGE", "Judge"],
    ];

    const stages =
      lanes.map(
        ([lane, label]) => {
          const status =
            stageStatus(lane);

          const cls =
            status === "COMPLETED"
              ? " complete"
              : "";

          return `
            <div class="v62-stage${cls}">
              <strong>${label}</strong>
              <span>${esc(status)}</span>
            </div>
          `;
        }
      ).join("");

    const paidPreflight =
      hasInitialPreflight
        ? `
          <div class="v62-paid-preflight">
            <div class="v62-paid-preflight-head">
              <strong>
                INITIAL COST PREFLIGHT ·
                ${esc(initial.call_count_planned)} CALLS
              </strong>
              <span class="v62-paid-preflight-cost">
                MAX $${esc(initial.estimated_max_cost_usd)}
              </span>
            </div>

            <div class="v62-paid-preflight-meta">
              ${esc(initial.model)}
              · ${esc(initial.reasoning_effort)}
              · request set
              ${esc(shortHash(initial.request_set_hash))}
              <br>
              OWNER APPROVAL:
              ${approved ? "GRANTED" : "NOT GRANTED"}
              · MODEL AUTHORITY: NO
              · AUTOMATIC RETRIES: 0
            </div>

            ${
              approved
                ? `
                  <div class="v62-approval-box approved">
                    <div class="v62-approval-title">
                      ✓ INITIAL PREFLIGHT APPROVED
                    </div>

                    <div class="v62-approval-copy">
                      Approval
                      ${esc(shortHash(approval.approval_hash))}
                      is bound to this exact request set
                      and MAX $${esc(approval.approved_max_cost_usd)}.
                      <br>
                      NO CHARGE YET · EXECUTOR: NOT PRESENT
                      · MODEL AUTHORITY: NO
                    </div>
                  </div>
                `
                : `
                  <div class="v62-approval-box">
                    <div class="v62-approval-title">
                      OWNER APPROVAL REQUIRED
                    </div>

                    <div class="v62-approval-copy">
                      This approval only records permission
                      for the exact frozen Initial request set.
                      It does not call OpenAI and cannot spend money.
                    </div>

                    <button
                      class="v62-button"
                      id="v62InitialApproval"
                      ${STATE.approvalBusy ? "disabled" : ""}
                    >
                      ${
                        STATE.approvalBusy
                          ? "RECORDING HASH-BOUND APPROVAL"
                          : `APPROVE INITIAL PREFLIGHT · MAX $${esc(initial.estimated_max_cost_usd)} · NO CHARGE YET`
                      }
                    </button>

                    ${
                      STATE.approvalError
                        ? `
                          <div class="v62-preflight-error">
                            ${esc(STATE.approvalError)}
                          </div>
                        `
                        : ""
                    }
                  </div>
                `
            }

            <button
              class="v62-button"
              id="v62InitialPreflight"
              ${STATE.preflightBusy ? "disabled" : ""}
            >
              ${
                STATE.preflightBusy
                  ? "FREEZING READ-ONLY EVIDENCE"
                  : approved
                    ? "REFRESH PREFLIGHT · CREATES NEW APPROVAL GATE"
                    : "REFRESH INITIAL COST PREFLIGHT"
              }
            </button>

            ${
              STATE.preflightError
                ? `
                  <div class="v62-preflight-error">
                    ${esc(STATE.preflightError)}
                  </div>
                `
                : ""
            }
          </div>
        `
        : `
          <div class="v62-paid-preflight">
            <div class="v62-paid-preflight-head">
              <strong>
                REAL INITIAL · ZERO-CALL GATE
              </strong>
              <span class="v62-paid-preflight-cost">
                NOT FROZEN
              </span>
            </div>

            <div class="v62-paid-preflight-meta">
              Freeze read-only market/account evidence,
              build the exact 9 bounded Initial requests,
              hash them and calculate the maximum cost.
              OpenAI calls remain 0.
            </div>

            <button
              class="v62-button"
              id="v62InitialPreflight"
              ${STATE.preflightBusy ? "disabled" : ""}
            >
              ${
                STATE.preflightBusy
                  ? "FREEZING READ-ONLY EVIDENCE"
                  : "FREEZE INITIAL COST PREFLIGHT"
              }
            </button>

            ${
              STATE.preflightError
                ? `
                  <div class="v62-preflight-error">
                    ${esc(STATE.preflightError)}
                  </div>
                `
                : ""
            }
          </div>
        `;

    const result =
      completed
      && STATE.run?.decision
        ? `
          <div class="v62-result">
            <strong>
              ${esc(
                STATE.run.decision.outcome
              )}
            </strong>
            <span>
              SIMULATION ONLY · NON-CANONICAL ·
              MODEL CALLS 0
            </span>
          </div>
        `
        : "";

    return `
      <div class="v62-analysis-kicker">
        INTERACTIVE COUNCIL · V6.2B
      </div>

      <h3>
        Analyze ${esc(symbol)}
        with Market Jury
      </h3>

      <div class="v62-analysis-copy">
        Real Initial requests are gated by
        a frozen cost preflight and explicit
        hash-bound owner approval.
        No paid model executor exists yet.
        The simulation controls below remain local-only.
      </div>

      <div class="v62-call-plan">
        <strong>Real Council topology:</strong>
        Initial ${esc(plan?.initial?.calls ?? 9)}
        + Rebuttal ${esc(plan?.rebuttal?.calls ?? 3)}
        + Judge ${esc(plan?.judge?.calls ?? 1)}
        = ${esc(plan?.total_calls ?? 13)} calls.
        Real paid authority: NO.
        This simulation: $0.
      </div>

      ${paidPreflight}

      <div class="v62-stages">
        ${stages}
      </div>

      <button
        class="v62-button"
        id="v62Start"
        ${running ? "disabled" : ""}
      >
        ${
          running
            ? "SIMULATION RUNNING"
            : completed
              ? `RUN LOCAL SIMULATION FOR ${esc(symbol)} AGAIN`
              : `RUN LOCAL SIMULATION FOR ${esc(symbol)}`
        }
      </button>

      ${result}
    `;
  }

  function ensureMarketPanel() {
    if (
      window.location.pathname !== "/"
    ) {
      return;
    }

    const host =
      document.getElementById("aiPanel");

    if (!host) {
      return;
    }

    let panel =
      document.getElementById(
        "v62AnalysisPanel"
      );

    if (!panel) {
      panel =
        document.createElement("div");

      panel.id = "v62AnalysisPanel";
      panel.className = "v62-analysis";

      host.appendChild(panel);
    }

    panel.innerHTML =
      panelHtml();

    const button =
      document.getElementById(
        "v62Start"
      );

    if (button) {
      button.onclick =
        start;
    }

    const preflightButton =
      document.getElementById(
        "v62InitialPreflight"
      );

    if (preflightButton) {
      preflightButton.onclick =
        captureInitialPreflight;
    }

    const approvalButton =
      document.getElementById(
        "v62InitialApproval"
      );

    if (approvalButton) {
      approvalButton.onclick =
        approveInitialPreflight;
    }
  }

  function ensureProductContext() {
    if (
      window.location.pathname === "/"
    ) {
      return;
    }

    const page =
      document.querySelector(".page-title");

    if (!page) {
      return;
    }

    let node =
      document.getElementById(
        "v62ProductContext"
      );

    if (!node) {
      node =
        document.createElement("div");

      node.id = "v62ProductContext";
      node.className =
        "v62-product-context";

      page.insertAdjacentElement(
        "afterend",
        node
      );
    }

    const symbol =
      STATE.session?.symbol || "—";

    if (!STATE.run?.available) {
      node.innerHTML = `
        <strong>
          ACTIVE SESSION · ${esc(symbol)}
        </strong>
        · no V6.2 product Council run stored
      `;
      return;
    }

    node.innerHTML = `
      <strong>
        ACTIVE SESSION · ${esc(symbol)}
      </strong>
      · V6.2 ${esc(STATE.run.mode)}
      · ${esc(STATE.run.status)}
      · canonical: NO
    `;
  }

  function render() {
    style();
    ensureMarketPanel();
    ensureProductContext();
  }

  async function refresh() {
    const [
      session,
      preflight,
      run,
      initialPreflight,
      initialApproval,
    ] = await Promise.all([
      json("/api/product/session"),
      json(
        "/api/product/analysis/preflight"
      ),
      json(
        "/api/product/analysis/current"
      ),
      json(
        "/api/product/analysis/initial/preflight"
      ),
      json(
        "/api/product/analysis/initial/approval"
      ),
    ]);

    STATE.session = session;
    STATE.preflight = preflight;
    STATE.run = run;
    STATE.initialPreflight =
      initialPreflight;
    STATE.initialApproval =
      initialApproval;

    render();

    if (
      run?.available
      && run.status === "RUNNING"
    ) {
      void loop();
    }
  }

  async function captureInitialPreflight() {
    if (STATE.preflightBusy) {
      return;
    }

    STATE.preflightBusy = true;
    STATE.preflightError = null;

    render();

    try {
      const value =
        await json(
          "/api/product/analysis/initial/preflight/capture",
          {
            method: "POST",
          }
        );

      STATE.initialPreflight = {
        ...value,
        available: true,
      };
    } catch (error) {
      STATE.preflightError =
        String(
          error?.message
          || error
        );
    } finally {
      STATE.preflightBusy = false;
      render();
    }
  }


  async function approveInitialPreflight() {
    if (
      STATE.approvalBusy
      || STATE.initialPreflight?.available !== true
    ) {
      return;
    }

    const initial =
      STATE.initialPreflight;

    STATE.approvalBusy = true;
    STATE.approvalError = null;

    render();

    try {
      const value =
        await json(
          "/api/product/analysis/initial/approval",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              preflight_id:
                initial.preflight_id,

              preflight_artifact_hash:
                initial.artifact_hash,

              request_set_hash:
                initial.request_set_hash,

              approved_max_cost_usd:
                initial.estimated_max_cost_usd,

              confirmation:
                "APPROVE_INITIAL_PREFLIGHT_NO_EXECUTION",
            }),
          }
        );

      STATE.initialApproval = {
        ...value,
        available: true,
      };
    } catch (error) {
      STATE.approvalError =
        String(
          error?.message
          || error
        );
    } finally {
      STATE.approvalBusy = false;
      render();
    }
  }


  async function start() {
    if (STATE.looping) {
      return;
    }

    STATE.run =
      await json(
        "/api/product/analysis/fake/start",
        {
          method: "POST",
        }
      );

    STATE.run.available = true;

    render();

    await loop();
  }

  async function loop() {
    if (
      STATE.looping
      || !STATE.run?.run_id
      || STATE.run.status !== "RUNNING"
    ) {
      return;
    }

    STATE.looping = true;

    try {
      while (
        STATE.run.status === "RUNNING"
      ) {
        await sleep(650);

        STATE.run =
          await json(
            "/api/product/analysis/fake/"
            + encodeURIComponent(
              STATE.run.run_id
            )
            + "/step",
            {
              method: "POST",
            }
          );

        STATE.run.available = true;

        render();
      }
    } finally {
      STATE.looping = false;
      render();
    }
  }

  async function boot() {
    try {
      await refresh();
    } catch (error) {
      console.error(
        "V6.2 analysis client:",
        error
      );
    }

    setInterval(
      () => {
        void refresh();
      },
      2500
    );

    setInterval(
      render,
      700
    );
  }

  if (
    document.readyState === "loading"
  ) {
    document.addEventListener(
      "DOMContentLoaded",
      boot,
      {once:true}
    );
  } else {
    void boot();
  }
})();
