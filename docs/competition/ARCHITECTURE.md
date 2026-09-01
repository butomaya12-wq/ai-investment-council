# AI Investment Council Architecture

The completed competition path is highlighted: the evidence-complete Judge selected **WATCH**, so the system stopped before B5 and before any broker side effect.

```mermaid
flowchart LR
    subgraph DATA[DATA]
        A[Alpaca / SEC / deterministic values]
        B[Eligibility and evidence layer]
        A --> B
    end

    subgraph RESEARCH[RESEARCH]
        C[Exactly 3 candidates]
        D[B3 evidence synthesis]
        B --> C --> D
    end

    subgraph COUNCIL[COUNCIL]
        E[Bull]
        F[Bear]
        G[Red Team]
        D --> E
        D --> F
        D --> G
    end

    subgraph ADVERSARIAL[ADVERSARIAL REVIEW]
        H[Rebuttal / cross-examination]
        E --> H
        F --> H
        G --> H
    end

    subgraph DECISION[DECISION]
        I[Bounded Judge]
        H --> I
    end

    I --> J[INVEST]
    J --> K[Deterministic option / risk]
    K --> L[Explicit human approval]
    L --> M[Alpaca PAPER execution]
    I --> N[WATCH]
    N --> O[Monitor / stop before execution]
    I --> P[ABSTAIN]
    P --> Q[Stop]

    classDef completed fill:#d8f3dc,stroke:#2d6a4f,stroke-width:3px,color:#081c15;
    classDef stop fill:#fff3bf,stroke:#9c6b00,stroke-width:2px,color:#3d2b00;
    class N,O completed;
    class P,Q stop;
```

`WATCH` is a valid decision outcome, not a failed execution. It keeps the execution boundary closed when the frozen policy cannot prove positive executable INVEST authority.
