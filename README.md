# 사용자 권한 조회·관리를 제공하는 Spring AI MCP 데모

**`cd BE && ./gradlew verify`** 로 실제 MCP·API·JWT 통합 검증을 실행합니다.
Java 21, Python 3, 첫 빌드의 의존성 다운로드가 필요합니다. MySQL 실행에는 Docker Compose가 필요합니다.
FE 원본과 사용법은 [FE/README.md](FE/README.md), 백엔드 소스와 실행 명령은 `BE/` 기준입니다.

## 실행

1. 백엔드 빌드와 개발용 자격 증명을 준비합니다.

   ```sh
   cd BE
   ./gradlew build
   java tools/DemoCredentials.java .dev/demo --fixtures
   ```

   새 디렉터리에만 생성합니다. MCP 사용자 토큰은 60분 수명이며 `mcp-alice.jwt`, `mcp-bob.jwt`, `mcp-carol.jwt`, `mcp-local-id-demo.jwt`, `mcp-admin.jwt`입니다.
   만료 후 새 디렉터리에 생성하고 두 앱의 `DEMO_DIR`를 그 절대 경로로 지정해 재시작합니다.
   두 앱 모두 `DEMO_DIR` 기본값은 실행 디렉터리 기준 `.dev/demo`입니다. 다른 디렉터리를 쓸 때는 API와 MCP를 시작하는 **각 터미널에서 같은 절대 경로**를 설정하세요.

   ```sh
   export DEMO_DIR=/absolute/path/to/my-mcp-server/BE/.dev/new-demo
   ```

   생성기는 macOS/Linux 개발 전용이며 디렉터리 `700`, 파일 `600`을 사용합니다. 실제 계정의 자격 증명을 읽지 않습니다.

2. MySQL을 시작합니다. `.env`의 예시 비밀번호를 로컬 전용 값으로 바꾸세요.

   ```sh
   cp .env.example .env
   # .env 편집: MYSQL_USER는 root가 아닌 전용 계정
   docker compose --env-file .env config --quiet
   docker compose --env-file .env up --build --wait access-db
   docker compose ps
   ```

   공식 MySQL `8.4.13` 기반 Dockerfile을 빌드합니다. 포트는 `127.0.0.1:3307`, Compose 프로젝트는 `mcp-access-demo`입니다.
   데이터는 전용 named volume `mcp-access-demo_access-data`에 유지됩니다. 비밀번호는 이미지/소스에 넣지 않습니다.

3. 터미널 A에서 `.env`와 같은 `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `DB_PORT`를 환경변수로 설정하고 API를 시작합니다.

   ```sh
   java -jar api-server/build/libs/api-server-0.1.0.jar --spring.profiles.active=local
   ```

   API는 `127.0.0.1:8081`입니다. `local` 프로필은 실제 JDBC/MySQL을 사용하고 Flyway가 스키마와 개발 seed를 적용합니다.
   DB 연결/마이그레이션 실패 시 기동 실패하며 인메모리로 우회하지 않습니다. `local,fixture` 동시 활성화도 기동 거부합니다.
   Docker 없이 인증·Tool 데모만 실행할 때는 **명시적으로** `--spring.profiles.active=fixture`를 사용하세요. 이 경우 MySQL 검증은 아닙니다.

4. 터미널 B에서 MCP를 시작합니다.

   ```sh
   java -jar mcp-server/build/libs/mcp-server-0.1.0.jar
   ```

   **http://127.0.0.1:8080/chat.html** 을 열고 사용자용 `mcp-alice.jwt` 내용을 입력합니다.
   연결 전 활용 안내에서 목적을 고르고, 연결 후 실제 본인 권한 경로와 조회 가능한 카탈로그를 확인할 수 있습니다. 목록에서 관리 대상을 골라도 미리보기와 명시적 실행을 거쳐야 변경됩니다.
   “내 역할은 뭐야?”, “접근 가능한 리소스 알려줘”, “R100에 접근할 수 있어?”를 사용할 수 있습니다.
   LLM 없는 결정적 질문 분류 데모이며 일반 AI 챗봇이 아닙니다. 관리는 별도 폼에서 대상·동작을 미리 보고 실행 버튼으로만 전송합니다. API 토큰과 개인키를 화면에 넣지 마세요.

5. 종료할 때 앱을 `Ctrl+C`로 종료하고 DB는 볼륨을 보존합니다.

   ```sh
   docker compose stop access-db
   ```

   `down -v`, `prune`, 데이터 초기화는 사용하지 않습니다.

## Tool ↔ API ↔ DB

| Tool / 입력 | 고정 호출 | 결과 |
|---|---|---|
| `get_my_access({})` | `GET /api/me` | 내 프로필·역할·정책·허용 리소스·부여 경로 |
| `check_my_resource_access({"resourceId":"R100"})` | `GET /api/me/resources/R100/access` | 접근 여부와 해당 역할·정책 경로 |
| `explain_authorization({"concept":"role"})` | API 호출 없음 | 정적 개념 설명, 실제 사용자 권한 아님 |
| `list_access_entities({entity})`, `get_access_entity({entity,id})` | `GET /api/catalog/{entity}[/{id}]` | 일반 사용자의 본인 그래프 / DB 관리자의 전체 카탈로그 |
| `get_requestable_roles({})` | `GET /api/me/requestable-roles` | 직접 보유하지 않은 신청 가능·비보호 역할의 공개 메타데이터 |
| `assign_role_to_user`, `remove_role_from_user` | `PUT/DELETE /api/manage/users/{userId}/roles/{roleId}` | 사용자–역할 관계 변경 |
| `attach_policy_to_role`, `detach_policy_from_role` | `PUT/DELETE /api/manage/roles/{roleId}/policies/{policyId}` | 역할–정책 관계 변경 |
| `attach_resource_to_policy`, `detach_resource_from_policy` | `PUT/DELETE /api/manage/policies/{policyId}/resources/{resourceId}` | 정책–리소스 관계 변경 |

`resourceId`는 `^R[0-9]{3}$`, `concept`는 `overview/permission/role/policy/resource`만 허용합니다.
12개 Tool 모두 추가 인수를 거부합니다. `entity`는 users/roles/policies/resources, 일반 대상 ID는 `[A-Za-z0-9_-]{1,64}`입니다.
관리의 `userId` 등은 변경 대상이며 인증 주체가 아닙니다. URL·임의 헤더·메서드를 받는 Tool은 없습니다.
성공 결과는 `structuredContent`와 호환 JSON text를 함께 제공합니다.

| 코드 | 책임 |
|---|---|
| `BE/mcp-server/.../ToolRegistration.java`, `AccessTools.java` | 스키마·메서드 권한·입력 검증·안전한 Tool 오류 |
| `AccessApiClient.java`, `DelegatedTokenIssuer.java` | 고정 REST 호출·검증된 사용자 위임·타임아웃·응답 검증 |
| `BE/api-server/.../AccessController.java`, `JdbcAccessRepository.java` | 최종 사용자 매핑·DB 기반 접근 권한과 경로 계산 |
| `BE/security-support/.../ResourceSecurity.java` | JWT·HTTP 보안, 공통 DTO는 같은 라이브러리의 `contract` 패키지 |
| `BE/api-server/.../db/migration`, `db/local` | Flyway 스키마와 분리된 local 전용 seed |

데이터는 User → Role → Policy → Resource의 **허용 전용 모델**입니다. 엔티티와 grant 쌍은 중복 제거하고 서로 다른 경로는 유지합니다.
Alice는 R100/R200/R300, Bob은 R100/R400, Carol은 프로필과 빈 권한 목록을 받습니다.
공유 `policy-shared`도 Alice에게는 analyst/editor 경로, Bob에게는 billing 경로만 표시됩니다.
금지 리소스와 미존재 리소스는 모두 `200 {resourceId,allowed:false,resource:null,grants:[]}` 형태이며 이름·설명·존재 이유를 숨깁니다.
미등록 사용자는 고정 `403 FORBIDDEN`이며, 역할 없는 등록 사용자 Carol과 구별됩니다.
별도 주체 `local-id-demo`의 로컬 ID는 `U900`입니다. subject와 로컬 ID가 같다고 가정하지 않습니다.
카탈로그의 비공개/없는 상세 항목은 동일 `404 NOT_FOUND`이며, 신청 가능 역할 목록은 정책·리소스·타인 사용자 정보를 공개하지 않습니다.

관계 변경에는 클라이언트 `authz.manage`와 API가 매번 조회하는 보호된 `SYSTEM_ADMIN` 역할이 **둘 다** 필요합니다.
`SYSTEM_ADMIN`, `SYSTEM_POLICY`, `R900` 등 보호된 링크 끝점은 관리자도 추가·해제할 수 없습니다.
관리자는 local seed의 독립 사용자 `admin`이며, scope만 가진 일반 사용자는 관리자가 되지 않습니다.
중복 추가/없는 연결 해제는 `changed:false`인 정상 결과입니다. `present`는 해당 트랜잭션 직후의 상태이고 이후 요청까지 고정한다는 뜻은 아닙니다.
`impact.affectedUserIds`는 잠재적 영향 대상이며, 다른 부여 경로 때문에 실제 접근 권한이 유지될 수 있습니다.
API는 보호 시스템 역할 행을 잠금 기준으로 사용해 작은 데모의 쓰기를 직렬화하고, 주체/관리자/보호 검사·변경·영향 계산·감사 기록을 하나의 트랜잭션에서 처리합니다.
`access_audit`에는 검증된 actor issuer/subject/local ID·동작·대상·시간·success/changed를 저장합니다. 감사 INSERT 실패도 관계 변경과 함께 rollback됩니다.
fixture 프로필의 관계 변경은 메모리 상태만 검증하며 **영속 감사 기록의 근거가 아닙니다**.

## 두 인증 구간과 신뢰 경계

| 구간 | 인증 및 권한 |
|---|---|
| 클라이언트 → MCP | RS256·고정 공개키, issuer `https://demo-issuer.invalid`, audience `access-mcp`, 필수 exp/문자열 nonblank sub, 선택 nbf; HTTP `mcp.connect`, 읽기 6개 `authz.read` / 쓰기 6개 `authz.manage` |
| MCP → API | 별도 키로 서명한 JWT, issuer `https://demo-delegation.invalid`, audience `access-api`, 고정 `act.sub=access-mcp`, 검증된 `original_iss`, 동작별 고정 scope `access.read` 또는 `access.manage`, 검증된 사용자 sub |

MCP는 Spring Security가 검증한 issuer+subject만 사용합니다. 들어온 Bearer와 임의 act/original_iss/scope/grants를 downstream으로 복사하지 않습니다.
API 위임 JWT의 만료는 `min(현재+60초, 원래 사용자 토큰의 exp)`입니다. API도 서명·issuer·audience·시간·actor·원issuer·주체 형식과 최대 60초 수명을 확인합니다.
클라이언트 scope는 Tool 사용 허가일 뿐 **DB의 도메인 권한이 아닙니다**. API는 검증된 원issuer+subject로 사용자를 찾고 본인 역할에서만 경로를 계산합니다.
DB identity는 VARBINARY로 정확히 비교하며 trim/lowercase/유니코드 정규화를 하지 않습니다. `X-User-Id`와 UI 선택값은 권한 근거가 아닙니다.

**위임 개인키를 가진 MCP는 사용자 신원을 주장할 수 있는 신뢰 주체입니다.** API는 원래 사용자 JWT 대신 MCP의 별도 서명 주장을 검증합니다.
이는 로컬 데모 위임이며 정식 OAuth Token Exchange/OBO 구현이 아닙니다. 유효기간 안의 재사용 방지/폐기 목록과 자동 키 회전은 구현하지 않았습니다.
일반 생성은 클라이언트 발급자 개인키를 저장하지 않습니다. `--fixtures`는 같은 키로 테스트 직전 재서명하기 위해 `fixture-issuer-private.pem`도 저장합니다.
`delegation-private.pem`은 MCP 서명을 위해 저장됩니다. 두 개인키 모두 backend-only 개발 파일이며 API·FE·JAR·로그에 포함하지 않습니다.
테스트 fixture만 갱신할 때는 `java tools/DemoCredentials.java <기존fixture디렉터리> --refresh-fixtures`를 사용합니다. 공개키/개인키를 바꾸지 않고 JWT 파일만 원자적으로 교체합니다.
관리 scope는 읽기를 포함하지 않습니다. 관리자 manage-only는 API/Tool 쓰기만, read-only는 전체 카탈로그 조회만 가능합니다.
UI 관리 미리보기는 인증된 주체를 `get_my_access`로 확인하므로 읽기+쓰기 scope가 필요하며, 전송 후 취소/통신 단절은 rollback을 뜻하지 않습니다.

전송은 **WebMVC + SYNC + stateless Streamable HTTP**(`protocol=STATELESS`)입니다. MCP 세션/로그인 쿠키 없이 요청마다 인증합니다.
Servlet 동기 경로가 주입된 Spring 보안 프록시를 호출합니다. 단절 시 이미 시작한 읽기는 즉시 취소가 보장되지 않고 downstream 제한 시간까지 남을 수 있습니다.
직접 Bearer HTTP/같은 출처 UI를 지원하며 OAuth 발견·로그인·보호 자원 메타데이터·자동 토큰 갱신은 미구현입니다.

## 안전성 및 데이터 초기화

| 항목 | 구현 |
|---|---|
| HTTP 요청 | loopback 바인딩, MCP POST 본문 16KiB 제한(chunked/인코딩 경로 포함), SDK 오류 원문 정제 |
| downstream | 고정 목적지/경로·GET/PUT/DELETE, redirect 금지, 연결 1초·헤더/본문 2초, 응답 16KiB 제한·DTO 관계 검증 |
| 브라우저 | GET `/chat.html`, `/chat.js`, `/chat.css`만 공개, 정확한 같은 Origin의 `/mcp` POST만 허용, API는 모든 Origin 거부, CSP self |
| 오류/로그 | Tool `isError=true` + 고정 코드, 내부 예외·토큰·추가 필드 미노출, 원문을 기록하는 SDK logger 3곳 비활성화 |
| DB | parameter binding, FK/복합 PK·identity unique/index, Flyway clean 비활성화, schema와 local seed 경로 분리 |

정상 기동 후 같은 volume에 API를 다시 시작해도 Flyway history로 중복 seed를 넣지 않습니다.
`data.sql`, 컨트롤러 초기 데이터 삽입, truncate/reseed는 없습니다. 기본/비local 프로필은 스키마만 읽고 local seed를 스캔하지 않습니다.
개발 DB 계정은 전용 DB 안에서 migration·조회·관계 변경·감사 기록을 수행합니다. 운영에서는 migration 계정을 분리하고 앱 계정에는 필요한 테이블의 조회·관계 변경·감사 INSERT 권한만 부여하세요.

HTTP 인증 실패는 401, 진입 권한 부족은 403입니다. Tool 오류는 `FORBIDDEN`, `INVALID_ARGUMENT`, `UPSTREAM_ACCESS_DENIED`,
`UPSTREAM_UNAVAILABLE`, `UPSTREAM_INVALID_RESPONSE`, `NOT_FOUND`, `INTERNAL_ERROR` 중 하나입니다. 리소스 접근 거부는 정상 boolean 결과입니다.

## 호출과 검증

아래는 `BE/`에서 실행합니다. 토큰은 요청 헤더에만 넣고 출력하지 않습니다.

```sh
MCP_TOKEN="$(cat .dev/demo/mcp-alice.jwt)"
curl -sS http://127.0.0.1:8080/mcp \
  -H "Authorization: Bearer $MCP_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl-demo","version":"1"}}}'
curl -sS http://127.0.0.1:8080/mcp \
  -H "Authorization: Bearer $MCP_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_my_access","arguments":{}}}'
unset MCP_TOKEN
```

| 명령 | 실제 검증 범위 |
|---|---|
| `./gradlew build` | Java 컴파일·실행 JAR·의존성 해결 |
| `./gradlew verify` | 명시 fixture 프로필, 실제 JVM·HTTP·MCP·JWT·메서드 권한·위임·동시 사용자 격리·오류/타임아웃·로그 |
| `python3 scripts/verify_mysql.py` | Docker DB build/up + local 프로필 통합 검증 2회 + Flyway checksum/history·seed 수 동일성, volume 보존 |
| 루트의 `node --test FE/tests/chat.test.mjs` | FE 단위 검증(실브라우저 검증과 별개) |

v2 개발 검증은 17개 통합 테스트(실제 fixture 프로필 JVM·HTTP)와 DTO 검증 JUnit 2개가 통과했습니다. 12개 Tool·관리 상태·scope 조합 회귀를 포함하며 최신 결과 파일에서 재확인할 수 있습니다.
HTTP 통합 결과는 `BE/build/integration/results.txt`, MySQL 전용 결과는 `BE/build/mysql-verification/`입니다.
핵심 JWT와 API/MCP 경로는 mock으로 우회하지 않습니다. 지연·redirect·잘못된 응답·outbound 관찰에만 테스트 HTTP 서버를 사용합니다.
실제 v2 MySQL 변경·감사·동시성·기존 관리 UI는 TESTER의 격리 DB에서 검증했습니다. 핵심 38개 고유 시나리오가 여러 실행에 걸쳐 누적 PASS이며 단일 실행 38 PASS는 아닙니다: [최초 37 PASS·1 BLOCKED](BE/test-results/20261113T164357Z-c01e27c3/summary.md), [후속 5 PASS](BE/test-results/20261113T164713Z-25d8df90/summary.md), [회귀 6 PASS](BE/test-results/20261113T164816Z-fccff2cd/summary.md). 과거 v1·인메모리 결과는 이 MySQL 검증의 근거로 사용하지 않습니다.
최종 UI 패키지는 [별도 실제 검증 9 PASS 보고서](BE/test-results/20261113T170311Z-14f1c7fe/report.json)로 확인했습니다(FAIL/BLOCKED/SKIPPED 0). 범위는 새 UI 6개(안내·관계·카탈로그·관리·계정 전환·모바일)와 기존 관리 브라우저·결과 불명확 상태·로그 비밀정보 검사 3개입니다. 이 실행은 위 핵심 38개 전체의 재실행이 아닙니다.
[TESTER 실행 안내](BE/tests/README.md)의 파일 기반 시나리오 38개는 `BE/tests/`, 불변 결과는 `BE/test-results/<run-id>/report.json`과 `summary.md`에 있습니다.
관리 검증은 `.env.e2e.example`을 바탕으로 별도 `mcp-access-e2e-v2` 프로젝트·DB·volume와 loopback 3308을 사용하며 기존 개발 데이터에 실행하지 않습니다.
TESTER용 `.env.e2e`에는 예시의 DB 설정 5개만 넣습니다. 프로젝트 이름은 handoff manifest와 `docker compose -p mcp-access-e2e-v2 --env-file .env.e2e`로 지정하고, `DB_PORT=3308`로 개발 DB와 분리합니다.

```sh
# BE/에서 실행. 실제 runtime은 MANAGER의 TEST_READY 인계 후 TESTER가 담당합니다.
python3 tests/e2e/runner.py --list
E2E_PLAYWRIGHT_MODULE=/absolute/path/to/existing/playwright/index.mjs \
  python3 tests/e2e/runner.py --all --contract v2 --profile local --handoff .dev/tester-v2-handoff.json
# 후속 재검증에는 --previous-run <이전-run-id> 추가
```

Playwright 경로는 이미 설치된 모듈을 지정합니다. 이 예제는 전역 설치·로그인·기존 volume 삭제를 수행하지 않습니다.

활용 안내·권한 관계·카탈로그 선택 UI의 별도 6개 시나리오는 [UI discovery 실행 안내](BE/tests/README.md)의 “기능 안내·관계 탐색 UI 확장”을 따릅니다. 새 MCP JAR를 인계받은 뒤 `--category ui-discovery --contract v2 --profile local --handoff .dev/tester-ui-handoff.json --previous-run <기존-run-id>`로 실행하며, 기존 서버 검증 결과를 새 UI 통과로 간주하지 않습니다.

## 고정 버전과 운영 교체

Java 21, Gradle Wrapper 8.14.3(SHA-256 검증), Spring Boot 3.5.16, Spring AI 1.1.8 BOM, MCP SDK 0.18.3을 사용합니다.
Boot BOM이 Flyway 11.7.2와 Connector/J 9.7.0을 선택하며 `gradle.lockfile`로 전이 의존성을 고정합니다.
[Spring AI 공식 호환 문서](https://github.com/spring-projects/spring-ai/blob/v1.1.8/spring-ai-docs/src/main/antora/modules/ROOT/pages/getting-started.adoc),
[Streamable HTTP](https://docs.spring.io/spring-ai/reference/1.1/api/mcp/mcp-stateless-server-boot-starter-docs.html),
[JWT 검증](https://docs.spring.io/spring-security/reference/6.5/servlet/oauth2/resource-server/jwt.html),
[MySQL 공식 이미지](https://hub.docker.com/_/mysql), [Flyway MySQL 모듈](https://github.com/flyway/flyway/blob/main/documentation/Reference/Database%20Driver%20Reference/MySQL.md)를 기준으로 구성했습니다.

운영에서는 신뢰할 인증 서버·JWKS 회전, 위임 정책/폐기, backend-only 비밀 저장, 두 구간 HTTPS, 실제 Host/Origin/목적지 allowlist,
DB TLS와 최소 권한 앱 계정, 필요 시 MCP OAuth 발견/로그인 및 트래픽 제한을 별도로 적용해야 합니다.
원래 사용자 산출물 `spring-boot-mcp-guide.md`는 보존했으며 현재 계약은 이 README와 실행 코드가 기준입니다.

다음 행동: `cd BE && ./gradlew verify`를 실행하세요.
