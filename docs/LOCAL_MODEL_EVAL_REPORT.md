# SignalForge 本地模型评测报告

manual_review_status: not_performed

- 生成时间：`2026-08-10T09:22:22.360896Z`
- 模型：`qwen3.5:9b`
- digest：`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`
- 量化：`Q4_K_M`
- 发布日期：`2026-03-02`
- 发布来源：https://github.com/QwenLM/Qwen3.6
- 案例数：3，重复次数：3

## Configuration

- `endpoint`: `"http://localhost:11434"`
- `model_name`: `"qwen3.5:9b"`
- `context_length`: `16384`
- `temperature`: `0.0`
- `seed`: `42`
- `max_output_tokens`: `2048`
- `reasoning_enabled`: `true`
- `timeout_seconds`: `120.0`
- `orchestration_version`: `"decision-memo-v1"`

## Raw model metrics

| metric | numerator | denominator | rate |
| --- | ---: | ---: | ---: |
| `first_pass_json_object_rate` | 0 | 9 | 0.0 |
| `first_pass_domain_schema_rate` | 0 | 9 | 0.0 |
| `after_one_retry_schema_rate` | 0 | 9 | 0.0 |
| `unknown_citation_rate` | 0 | 0 | null |
| `visible_counter_omission_rate` | 0 | 0 | null |
| `numeric_text_block_rate` | 0 | 0 | null |
| `mechanism_rubric_rate` | 0 | 0 | null |
| `response_model_match_rate` | 0 | 0 | null |

- attempt_count: 18
- error_code_counts: `{"LOCAL_MODEL_TIMEOUT": 18}`

## Validated system metrics

| metric | numerator | denominator | rate |
| --- | ---: | ---: | ---: |
| `effective_output_rate` | 0 | 9 | 0.0 |
| `expected_status_match_rate` | 0 | 9 | 0.0 |
| `persisted_unknown_citation_rate` | 0 | 0 | null |
| `unsupported_numeric_claim_rate` | 0 | 0 | null |
| `redacted_reference_rate` | 0 | 0 | null |
| `evidence_link_integrity_rate` | 0 | 0 | null |
| `dataset_version_integrity_rate` | 0 | 0 | null |

- terminal_distribution: `{"actionable": 0, "needs_evidence": 0, "refused": 0, "failed": 9}`
- persisted_unknown_citation_count: 0
- unsupported_numeric_claim_count: 0
- redacted_reference_count: 0

## Runtime

- `cold_end_to_end`: `{"count": 1, "minimum_ms": 244916, "median_ms": 244916.0, "maximum_ms": 244916}`
- `warm_end_to_end`: `{"count": 8, "minimum_ms": 242876, "median_ms": 243673.0, "maximum_ms": 244281}`
- `provider_attempt_count`: `18`
- `retry_event_count`: `9`
- `input_token_estimate`: `5466`
- `json_object_received_count`: `0`
- `input_token_count`: `null`
- `generated_token_count`: `null`
- `model_total_duration_ns`: `null`
- `model_load_duration_ns`: `null`
- `input_evaluation_duration_ns`: `null`
- `generation_duration_ns`: `null`

## Case outcomes

| case | repeat | expected | terminal | error_code | end_to_end_ms |
| --- | ---: | --- | --- | --- | ---: |
| `coherent-service` | 1 | actionable | failed | LOCAL_MODEL_TIMEOUT | 244916 |
| `split-service` | 1 | needs_evidence | failed | LOCAL_MODEL_TIMEOUT | 242993 |
| `weak-positive` | 1 | refused | failed | LOCAL_MODEL_TIMEOUT | 244093 |
| `coherent-service` | 2 | actionable | failed | LOCAL_MODEL_TIMEOUT | 243632 |
| `split-service` | 2 | needs_evidence | failed | LOCAL_MODEL_TIMEOUT | 243714 |
| `weak-positive` | 2 | refused | failed | LOCAL_MODEL_TIMEOUT | 243631 |
| `coherent-service` | 3 | actionable | failed | LOCAL_MODEL_TIMEOUT | 243847 |
| `split-service` | 3 | needs_evidence | failed | LOCAL_MODEL_TIMEOUT | 244281 |
| `weak-positive` | 3 | refused | failed | LOCAL_MODEL_TIMEOUT | 242876 |

## Failures and refusals

- `coherent-service` repeat 1: failed (LOCAL_MODEL_TIMEOUT)
- `split-service` repeat 1: failed (LOCAL_MODEL_TIMEOUT)
- `weak-positive` repeat 1: failed (LOCAL_MODEL_TIMEOUT)
- `coherent-service` repeat 2: failed (LOCAL_MODEL_TIMEOUT)
- `split-service` repeat 2: failed (LOCAL_MODEL_TIMEOUT)
- `weak-positive` repeat 2: failed (LOCAL_MODEL_TIMEOUT)
- `coherent-service` repeat 3: failed (LOCAL_MODEL_TIMEOUT)
- `split-service` repeat 3: failed (LOCAL_MODEL_TIMEOUT)
- `weak-positive` repeat 3: failed (LOCAL_MODEL_TIMEOUT)

## Limitations

- 固定合成案例，不是生产流量。
- 机制深度是透明启发式，不是独立人工评分。
- 结果仅绑定于评测首尾一致的模型 tag、digest 与记录配置；未采集或验证硬件身份。

未进行独立人工复核；这些结果不能证明生产质量或业务影响。
