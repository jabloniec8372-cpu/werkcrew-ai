# Frozen M1 Lifecycle Contract v1

- **Status:** USTALONE
- **Zakres:** canonical M1 creation, idempotency, follow-up evidence, conversation association, DORMANT i wake tego samego JOB

## Decyzja

1. Canonical `job_id` jest stały. `activity_state` jest osobnym wymiarem `ACTIVE | DORMANT`; nie zastępuje `lifecycle_state` i nie czyści stanu workflow, faktów, evidence, provenance ani bindings.
2. Każda mutacja M1 ma jawny `operation_kind`: `CREATE_JOB`, `RECORD_FOLLOW_UP`, `MARK_DORMANT`, `WAKE_JOB` albo `BIND_CONVERSATION`. Rodzaju operacji ani durable targetu nie wybiera raw content, podobieństwo ani LLM.
3. Globalny klucz idempotency M1 to `(source_namespace, ingress_event_id)`. Fingerprint `m1-operation-v1` jest SHA-256 canonical JSON wszystkich business-relevant submitted inputs, bez server-generated timestamps, IDs i resultu.
4. Ten sam klucz i fingerprint zwraca dokładnie pierwszy persisted result. Zmieniony fingerprint lub operation kind zwraca `EVENT_CONFLICT` bez nowego business effect. Terminalne sukcesy i odrzucenia korelacji/stanu są trwałe.
5. `CREATE_JOB` zawsze oznacza nowe zlecenie. `RECORD_FOLLOW_UP` nigdy nie tworzy JOB i wymaga jawnego `job_id` albo dokładnie jednego bindingu `(source_namespace, conversation_id)`. Zero, wiele albo sprzeczność kończą się fail-closed.
6. Conversation może być jawnie związana z wieloma JOB. Binding nie jest automatycznie przenoszony, naprawiany ani usuwany.
7. Tylko jawny `MARK_DORMANT` zmienia `ACTIVE -> DORMANT`. M1 v1 nie ma timerów, reminderów ani automatycznej oceny ciszy. Reason codes są wyłącznie audytowe.
8. `WAKE_JOB` lub authoritatively correlated `RECORD_FOLLOW_UP` zmienia wyłącznie `DORMANT -> ACTIVE` tego samego `job_id`. Follow-up do `ACTIVE` nie tworzy `JOB_WOKEN`.
9. Follow-up evidence jest append-only i zachowuje raw input osobno od immutable initial intake. Zweryfikowane deterministycznie lub przez człowieka fact candidates są jednym batch CAS; stale batch zachowuje evidence i wymagany wake, ale nie dopisuje żadnego faktu.
10. Historia `FOLLOW_UP_RECORDED`, `JOB_MARKED_DORMANT` i `JOB_WOKEN` jest append-only, per-job sequenced i połączona z operation/evidence. Pierwsze przetwarzanie odbywa się w jednej transakcji SQLite `BEGIN IMMEDIATE`.
11. SQLite przez `SqlitePersistence` pozostaje jedynym source of truth. `DemoWorkflowStore` jest Gen1-only.

## Poza zakresem

Automatyczne przypomnienia 2/7/14 dni, inactivity scheduler, fuzzy matching, LLM merge, conversation repair, M1→M2, SAFE_HOLD changes, tasks, assignments, worker ACK/timeouts, dispatch fallback i deployment pozostają poza M1 Lifecycle v1.
