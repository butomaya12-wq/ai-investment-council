(() => {
  "use strict";

  const STATE = {
    session: null,
    preflight: null,
    run: null,
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
        INTERACTIVE COUNCIL · V6.2A
      </div>

      <h3>
        Analyze ${esc(symbol)}
        with Market Jury
      </h3>

      <div class="v62-analysis-copy">
        This build validates the complete
        product workflow using local fake
        transport. It cannot create a real
        investment decision or broker action.
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
              ? `RUN ${esc(symbol)} FLOW AGAIN`
              : `ANALYZE ${esc(symbol)} WITH MARKET JURY`
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
    ] = await Promise.all([
      json("/api/product/session"),
      json(
        "/api/product/analysis/preflight"
      ),
      json(
        "/api/product/analysis/current"
      ),
    ]);

    STATE.session = session;
    STATE.preflight = preflight;
    STATE.run = run;

    render();

    if (
      run?.available
      && run.status === "RUNNING"
    ) {
      void loop();
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
