---
id: gotcha-otel-spanmetrics-connector
type: gotcha
title: "Gotcha: OTel collector spanmetrics is a connector, not a processor"
date: 2026-04-10
tags: [L2-platform-mlops, discovery]
related:
  - journey/03-observability
one_line: "spanmetrics moved from 'processor' to 'connector' in recent OTel collector versions. Use the connectors section in the collector config or the metrics pipeline will silently produce nothing."
---

# Gotcha: OTel collector spanmetrics is a connector, not a processor

## Symptom
```
error decoding 'processors': unknown type: "spanmetrics"
```

## Root cause
In older OTel collector versions, `spanmetrics` was a processor. In
recent versions (0.100+), it was moved to a **connector** — a new
component type that bridges between signal pipelines (traces → metrics).

## Fix
Use the `connectors` section, not `processors`:

```yaml
connectors:
  spanmetrics:
    namespace: gemma_forge
    dimensions:
      - name: gen_ai.request.model

service:
  pipelines:
    traces:
      exporters: [otlp/jaeger, spanmetrics]  # spanmetrics as exporter
    metrics:
      receivers: [spanmetrics]  # spanmetrics as receiver
```

The connector appears as an **exporter** in the traces pipeline and a
**receiver** in the metrics pipeline.

## Environment
- otel/opentelemetry-collector-contrib:0.121.0
