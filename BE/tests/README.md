# 파일 기반 실제 검증

`BE/`에서 `python3 tests/e2e/runner.py --list`로 v2 안정 ID와 실행 범위를 확인합니다. v1 시나리오는 `--contract v1 --list`로 따로 조회합니다. 기존 v1 결과는 v2 통과 근거가 아닙니다.
자연어 요청은 MAIN → MANAGER → TESTER로 전달하며, TESTER는 명시적인 `TEST_READY` 이후에만 runtime을 실행합니다.

1. MANAGER가 빌드 동결, 프로필, API/MCP JAR의 절대 경로와 SHA-256, 인계 PID/포트, fixture 경로, Compose 프로젝트/volume을 전달합니다. TESTER가 별도 sanitized manifest에 기록합니다. 기존 프로세스는 실제 명령과 cwd를 확인하고 명시적으로 양도된 것만 정상 종료합니다.
2. 오프라인 검증: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/e2e -p 'test_runner.py' -v` 및 `node --check tests/e2e/browser.mjs`.
3. 전체 실행: `E2E_PLAYWRIGHT_MODULE=/absolute/path/to/existing/playwright/index.mjs python3 tests/e2e/runner.py --all --contract v2 --profile local --handoff .dev/tester-v2-handoff.json`. 설치된 로컬 Playwright와 Chrome만 사용합니다. 브라우저 부재는 BLOCKED이며 API 결과로 대체하지 않습니다.
4. 선택 실행: `python3 tests/e2e/runner.py --id V2-AUTHORITY,V2-PROTECTED --profile local --handoff .dev/tester-v2-handoff.json`. 분류 선택은 `--category browser`; 재검증은 `--previous-run <이전-run-id>`를 추가합니다. 브라우저 선택 시에도 위 환경변수가 필요합니다.
5. 결과: `test-results/<UTC-time>-<random>/report.json`과 `summary.md`. FAIL/BLOCKED는 종료 코드 1, 시나리오 구성 오류는 2입니다. 이전 결과는 덮어쓰지 않습니다. MANAGER가 시나리오 ID·입력·기대/실제·담당을 BE/FE에 전달하고 새 빌드 인계 후 관련 ID를 재실행합니다.

## 인계 manifest 형식

아래 경로와 해시는 실제 인계 값으로 채웁니다. 토큰/키/비밀번호는 넣지 않습니다.

```json
{
  "status": "TEST_READY",
  "runtime_owner": "TESTER",
  "builds_frozen": true,
  "contract": "v2",
  "profile": "local",
  "artifacts": {
    "api-server": {"path": "/absolute/BE/api-server/build/libs/api-server-0.1.0.jar", "sha256": "actual-sha256"},
    "mcp-server": {"path": "/absolute/BE/mcp-server/build/libs/mcp-server-0.1.0.jar", "sha256": "actual-sha256"}
  },
  "compose_project": "mcp-access-e2e-v2",
  "volume": "mcp-access-e2e-v2_access-data",
  "isolated_mutations": true,
  "compose_env_file": "/absolute/BE/.dev/tester-v2.env",
  "audit_schema": {
    "table": "access_audit", "order": "id",
    "actor_id": "actor_user_id", "actor_issuer": "actor_issuer",
    "actor_subject": "actor_subject", "operation": "operation",
    "left_id": "left_id", "right_id": "right_id", "occurred_at": "occurred_at",
    "success": "success", "changed": "changed"
  },
  "fixture_path": "/absolute/BE/.dev/handed-off-fixtures",
  "previous_processes": [{"pid": 12345, "port": 8081}]
}
```

## v2 coverage matrix

`management-v2.json`의 38개 안정 ID는 다음 실행 행렬을 구성합니다. 여기의 준비 완료는 실제 통과를 의미하지 않습니다.

| 대상 | actor/scope | 상태와 근거 | 대표 시나리오 |
|---|---|---|---|
| self 2 GET, 개념, 12 Tool schema | alice/bob/carol/U900, read | API·MCP 일치, 독립 subject/ID, provenance, schema/annotation | V2-PROTOCOL, V2-SELF-ID, V2-PROVENANCE |
| catalog list/detail, requestable | ordinary/no-role/admin read-only/admin manage-only | own/full graph, hidden=missing404, 직접 역할과 유효 리소스 구별 | V2-CATALOG, V2-REQUESTABLE, V2-AUTHORITY |
| user-role / role-policy / policy-resource PUT·DELETE | admin both/manage-only, ordinary both, unknown, read-only | add/remove/no-op, invalid/missing/protected, current DB graph, 요청 거부 전후 불변 | V2-CYCLE-*, V2-PROTECTED, V2-INVALID, V2-MISSING-ATOMIC |
| 3 관계의 동시성·transaction·감사·재기동 | verified admin, 격리 MySQL | PUT/PUT·DELETE/DELETE·PUT/DELETE audit 대조, audit 삽입 실패 rollback, 실제 volume/history 보존 | V2-CONCURRENT-*, V2-AUDIT*, V2-RESTART |
| 브라우저 및 보안 경계 | 계정 전환, expired, read-only/manage-only | 실제 read 질문, 불변 preview/실행/취소/Enter/double-click, commit 후 응답 유실·손상·timeout·계정전환, Origin/Host/JWT/크기/로그 | V2-BROWSER-*, V2-WRITE-BOUNDARY, V2-JWT, V2-PROTOCOL-LIMITS, V2-LOG-SECRETS |

감사 rollback은 격리 MySQL의 테스트 CHECK 제약으로 INSERT 실패를 실제 발생시킵니다. 디스크 고장, DB crash recovery, 무한한 모든 동시 interleaving까지 검증했다는 의미는 아닙니다. 새 보고서에는 실제 실행된 handler와 관측 결과만 기록합니다.

실행 전 보호와 근거:

- API 자식 환경은 allowlist로 생성합니다. 상속된 `SPRING_*`, `JAVA_TOOL_OPTIONS`, `JDK_JAVA_OPTIONS`, `_JAVA_OPTIONS`는 제거하고 datasource/Flyway URL은 CLI에서 격리된 `127.0.0.1:3308/<DB>`로 고정합니다. 비밀번호는 환경변수로만 전달합니다. 외부 Spring config 파일이 있으면 실행을 차단합니다.
- [Connector/J connectionAttributes](https://docs.oracle.com/cd/E17952_01/connector-j-en/connector-j-connp-props-connection.html)에 실행 식별자를 넣습니다. 격리 MySQL의 Performance Schema에서 해당 API 접속과 DB명을 실제 관측해야 mutation handler가 열립니다. 컨테이너만 격리되었다는 이유로 API 격리를 통과시키지 않습니다.
- 동시성은 송신 barrier와 테스트 전용 row lock을 함께 사용합니다. Performance Schema에서 같은 테스트 lock을 기다리는 실제 서버 transaction을 관측하고, 응답 interval·operation/relationship/changed/present multiset을 audit 순서 재생과 대조합니다. 겹침을 관측하지 못하면 통과하지 않습니다. 테스트 connection의 lock은 ROLLBACK으로 해제합니다.
- Performance Schema 조회만 컨테이너 내부 root 계정으로 수행하며 grant나 전역 설정을 변경하지 않습니다. 앱 연결은 전용 non-root 계정입니다. 감사 실패 CHECK 제약 제거 후 동일 작업이 성공하는지도 확인합니다.
- 브라우저 fault 항목의 `backendSuccessResponseObserved`는 응답 유실 전 실제 MCP 성공 응답 관측을 뜻합니다. 모드별 DB commit을 별도로 증명한 것으로 표기하지 않습니다. 실제 DB persistence/atomicity는 독립 DB handler가 검증합니다.

runner는 `scripts/verify.py`의 등록된 검증 메서드를 재사용하며 편집하지 않습니다. 각 실행에서 새 private 임시 fixture를 생성하고, v2 검증 직전에 `DemoCredentials.java <동일dir> --refresh-fixtures`로 같은 키의 시간 민감 토큰을 재발급합니다. product TTL은 늘리지 않습니다. 검증된 JAR를 독점적인 임시 loopback 포트로 시작하고 생성한 JVM만 정리합니다. 기존/인계 프로세스를 runner가 임의로 종료하지 않습니다.

v2는 전용 env의 `DB_PORT=3308`, 별도 Compose 프로젝트와 named volume을 요구합니다. 기존 `mcp-access-demo`/`mcp-access-demo_access-data`에서는 실행을 거부합니다. 관계 변경은 별도 테스트 DB에서 수행하고 각 handler가 초기 관계 상태로 복구하며 성공/no-op 감사 기록은 유지합니다. 감사 저장 실패 검증은 서버 시각 cutoff 이전 기존 행을 모두 보존하는 테스트 전용 CHECK 제약을 추가해 새 audit INSERT를 실패시키고, 해당 제약만 제거한 뒤 동일 쓰기의 성공을 확인합니다. trigger 생성의 SUPER/binlog 권한 오류가 있었던 최초 BLOCKED 결과는 보존합니다. 사용자 DB/volume 삭제·reset·prune은 수행하지 않습니다.

정상 브라우저 검증과 `browser-fault`는 분리합니다. 후자는 실제 MCP/API/MySQL 쓰기 성공을 확인한 뒤 브라우저 응답만 유실·지연·손상시켜 결과 미확인/자동 재시도 금지/계정 전환을 검증합니다. 이를 정상 네트워크 E2E 결과로 혼합하지 않습니다. 검증 후 필요하면 정확한 프로젝트와 env를 지정한 `docker compose ... stop access-db`로 volume을 보존하며 정지합니다.

`local`은 실제 Docker MySQL/JDBC/Flyway를 검증합니다. `fixture`는 명시적인 별도 인메모리 프로필이며 MySQL 전용 항목은 SKIPPED입니다. `fault-injection`은 실제 MCP JVM과 제어된 테스트 upstream의 timeout/오류/위임 관측이며 MySQL 또는 전체 E2E 통과 근거가 아닙니다. 실제 브라우저는 response mocking 없이 입력·클릭 → same-origin MCP 요청/응답 → 렌더링을 검증합니다.

JSON은 메타데이터와 등록 handler만 지정합니다. shell/eval DSL은 없으며 잘못된 필드·중복/미등록 ID·없는 handler를 실행 전에 거부합니다. 보고서에는 프로필/해시/명령/fixture 이름/기대·실제/시간/근거 경로가 들어갑니다. raw server 로그는 private 임시 위치에서 비밀 검사를 거친 뒤 정제한 사본만 보관합니다. fixture 값, Authorization 헤더, private key와 DB 비밀번호는 결과에 저장하지 않습니다.

다음 행동: `python3 tests/e2e/runner.py --list`를 실행합니다.

## 기능 안내·관계 탐색 UI 확장

`discovery-v2.json`의 독립 6개 `UI-*` 시나리오는 새로 패키징한 MCP JAR에서만 실행합니다. 기존 v2 38개 통과 보고서는 새 UI 통과로 확장하지 않습니다. 새 TEST_READY manifest의 `ui_selectors`에는 `tests/e2e/discovery-selectors.json`의 확정 DOM 매핑을 넣고 새 MCP JAR 해시를 기록합니다. 필요한 selector가 없으면 BLOCKED입니다.

`python3 tests/e2e/runner.py --category ui-discovery --contract v2 --profile local --handoff .dev/tester-ui-handoff.json --previous-run <기존-run-id>`로 연결 전 5목적 안내, 실제 본인 grant 경로, 허용된 카탈로그/상세, 실제 목록의 관리 대상 선택→preview/취소/명시 실행→변경 반영, 권한·계정 격리, 390×844 모바일/키보드를 검증합니다. 기존 `E2E_PLAYWRIGHT_MODULE` 환경변수도 필요합니다. 모바일 스크린샷은 토큰 입력이 빈 상태에서만 저장합니다.

실제 사용 화면은 `--id UI-GRAPH,UI-MANAGE,V2-LOG-SECRETS`로 새 실행에 기록할 수 있습니다. `final-guide-and-self-access.png`는 Alice의 실제 조회와 활용 안내, `final-grant-paths.png`는 서버 응답과 정확히 일치하는 6개 경로, `final-explicit-management-success.png`는 admin이 Carol에게 분석가 역할을 명시 실행한 성공 화면입니다. 토큰 입력값이 비었음을 확인하고 연결 설정을 제외한 실제 결과 DOM 영역만 Playwright로 촬영합니다. 캡처 뒤 같은 관리 시나리오가 추가한 관계를 해제하며, 해당 실행의 보고서와 함께 이미지를 확인해야 합니다. 실제 runtime 재기동은 MANAGER의 TEST_READY와 TESTER 독점 소유권을 전제로 합니다.

## Env launcher 독립 검증

`BE/`에서 `PYTHONDONTWRITEBYTECODE=1 python3 tests/e2e/test_env_launcher.py`를 실행하면 독립 public CLI 계약을 검사합니다. 임시 복제 디렉터리의 가짜 Java는 인수·환경값의 SHA-256만 기록합니다. 실제 JVM·포트·Docker는 사용하지 않으며 이 결과를 MySQL/E2E 통과로 취급하지 않습니다. 문법, 무실행 literal, 파일 전체와 선택 shell의 placeholder 거부, 우선순위/빈 값, 경로 기준, 서버별 최소 환경, packaged config 고정과 잘못된 입력의 실행 전 거부를 검증합니다. 제품 launcher 단위검사와 별개의 테스트입니다.

실제 실행은 MANAGER TEST_READY 이후에만 다음 명령으로 수행합니다. `--authorization`에는 해당 인계 메시지 ID, `--handoff`에는 승인된 launcher/API/MCP 파일의 SHA-256 맵을 넣습니다. 경로와 옵션 외 자격증명 값을 명령행에 넣지 않습니다.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/e2e/launcher_runtime.py \
  --handoff /tmp/my-mcp-env-launcher-handoff.json \
  --authorization <TEST_READY-message-id> \
  --compose-env .dev/tester-v2.env \
  --previous-run <previous-run-id>
```

검증은 기존 healthy `mcp-access-e2e-v2`/`access_e2e_v2`/3308/`mcp-access-e2e-v2_access-data`만 관측합니다. container를 자동 생성·재초기화·삭제하지 않으며 기존 `.env.e2e`의 정확 5키 계약을 유지합니다. 임시 `BE/.dev/launcher-runtime-*/`에 byte-identical launcher/JAR와 새 독립 keys/fixtures를 생성하고, 원본 env/기존 자격증명 해시를 보존 확인합니다. 임시 cwd의 hostile properties/YAML/config 파일이 무시되는지 실제 loopback listener, API PID의 3308 DB socket, 새 Performance Schema 접속의 DB/사용자, 로그파일·sentinel 부재로 검증합니다.

실제 launcher 두 JVM의 MCP 초기화→12 Tools→Alice/Bob/Carol 권한 결과와 직접 API 일치, 인증거부, API 종료 후 MCP의 안전한 upstream 오류, 잘못된 DB의 startup 실패, SIGINT 종료·원본 불변·DB history/count 보존·argv/log 비밀 부재를 검사합니다. 실CLI 음성검사의 Java 관측 wrapper는 호출되면 실제 Java를 실행하며, placeholder/누락/잘못된 입력이 그 지점에 도달하지 않았음을 확인합니다. 사용자 volume은 보존하고 본 테스트가 시작한 process group만 정리합니다.

각 실행은 `BE/test-results/launcher-cli-*` 또는 `launcher-runtime-*` 아래 새 `report.json`과 `summary.md`를 남깁니다. 실패·미실행은 통과와 분리하며, 재실행은 `--previous-run`으로 연결합니다. 기존 core38/UI9 결과는 launcher 검증 근거가 아닙니다. 실제 사용 Python 버전을 기록하며 설치하지 않은 최소 버전의 실행을 주장하지 않습니다.
