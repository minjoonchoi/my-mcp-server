# 페이지·엔드포인트·필드 권한을 조회·관리하는 Spring AI MCP 데모

**`cd BE && ./gradlew verify`** 로 실제 MCP·API·JWT 통합 검증을 실행합니다.
JDK 21, Python 3.9 이상, 첫 빌드의 의존성 다운로드가 필요합니다. 기본 로컬 MySQL은 Docker Compose를 사용하며 격리 테스트는 별도로 준비한 MySQL 바이너리도 지원합니다.
FE 원본과 사용법은 [FE/README.md](FE/README.md), 백엔드 소스와 실행 명령은 `BE/` 기준입니다.

## 실행

1. `BE/`에서 빌드하고 env 예시를 **없는 파일에만** 복사합니다.

   ```sh
   cd BE
   ./gradlew build
   python3 - <<'PYENV'
   import os
   from pathlib import Path
   for name in ('.env', '.env.api', '.env.mcp'):
       try:
           fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
       except FileExistsError:
           print(name + ': 기존 파일 유지')
           continue
       with os.fdopen(fd, 'w') as output:
           output.write(Path(name + '.example').read_text())
       print(name + ': 예시 생성, 편집 필요')
   PYENV
   ```

   `.env`는 Compose 전용입니다. 전용 DB와 root가 아닌 사용자를 정하고 로컬 비밀번호를 편집합니다.
   `.env.api`의 `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `DB_PORT`를 `.env`의 네 값과 맞춥니다.
   root 비밀번호는 `.env`에만 둡니다. `.env.mcp`에는 DB 설정을 넣지 않습니다. `replace-with-...` 값은 런처가 거부합니다.
   Compose의 `--env-file`은 호스트 Java 프로세스에 환경변수를 주입하지 않으므로 서버별 파일을 별도로 읽습니다.

2. 호환되는 기존 키·계정 자료를 참조하거나, **새 디렉터리**에 개발용 자료를 생성합니다.

   ```sh
   java tools/DemoCredentials.java .dev/demo-login
   ```

   `.dev/demo-login`은 아직 존재하지 않는 경로의 예시입니다. 기존 디렉터리에는 덮어쓰지 않으며, 기존 키·비밀번호·계정을 자동 변경하지 않습니다.
   생성기는 디렉터리 `700`, 파일 `600`을 사용합니다. 기존 키 디렉터리에 `issuer-private.pem`이 없다면 파일을 덧붙이거나 기존 키를 재생성하지 말고 새 디렉터리를 사용하세요.
   `.env.api`와 `.env.mcp`의 키 경로를 다음처럼 **같은 키 세트**에 맞춥니다(경로는 각 env 파일의 위치 기준).

   | 파일 | 변수 | 새 경로 예시 |
   |---|---|---|
   | `.env.api` | `DEMO_PUBLIC_KEY` | `.dev/demo-login/delegation-public.pem` |
   | `.env.api` | `AUTH_PUBLIC_KEY`, `AUTH_PRIVATE_KEY` | `.dev/demo-login/issuer-public.pem`, `.dev/demo-login/issuer-private.pem` |
   | `.env.mcp` | `DEMO_PUBLIC_KEY` | `.dev/demo-login/issuer-public.pem` |
   | `.env.mcp` | `DELEGATION_PUBLIC_KEY`, `DELEGATION_PRIVATE_KEY` | `.dev/demo-login/delegation-public.pem`, `.dev/demo-login/delegation-private.pem` |

   `alice`, `bob`, `carol`, `local-id-demo`, `admin`의 서로 다른 임의 비밀번호는 비공개 `login-passwords.json`에 있습니다. 로컬 편집기로 필요한 계정의 비밀번호만 확인하세요.
   공통 기본 비밀번호는 없고 `login-accounts.json`·`login-accounts.sql`에는 해시와 계정 매핑만 저장합니다. 일반 생성과 `--fixtures` 모두 로그인 자료를 만들며 후자는 검증용 JWT도 추가합니다.
   수동 `mcp-*.jwt`는 60분, 로그인 토큰은 기본 15분 수명입니다. 로그인 토큰 만료 시 다시 로그인합니다. 런처는 키·토큰·계정 파일을 자동 생성하거나 갱신하지 않습니다.

3. Compose로 MySQL을 시작하고 API를 실행한 뒤 계정을 한 번 등록합니다.

   ```sh
   docker compose --env-file .env config --quiet
   docker compose --env-file .env up --build --wait access-db
   docker compose --env-file .env ps
   # 터미널 A
   python3 scripts/run_server.py api
   ```

   공식 MySQL `8.4.13`, 기본 `127.0.0.1:3307`, 프로젝트 `mcp-access-demo`, 보존 volume `mcp-access-demo_access-data`를 사용합니다.
   API는 기본 `127.0.0.1:8081`, 고정 `local` 프로필이며 실제 JDBC/MySQL과 Flyway schema·local seed를 사용합니다.
   DB 연결·인증·마이그레이션 실패 시 기동 실패하며 인메모리로 우회하지 않습니다. V7은 `login_accounts` 테이블만 만들고 계정을 자동 등록하지 않습니다.
   API 기동으로 V7 적용을 확인한 뒤, 별도 터미널의 `BE/`에서 **의도한 새 개발 DB에** 해시 SQL을 한 번만 등록합니다.

   ```sh
   docker compose --env-file .env exec -T access-db sh -c \
     'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --host=127.0.0.1 --user="$MYSQL_USER" --database="$MYSQL_DATABASE"' \
     < .dev/demo-login/login-accounts.sql
   ```

   전용 앱 계정과 stdin을 사용하며 비밀번호를 argv에 넣지 않습니다. 일반 INSERT이므로 중복 계정은 실패하고 기존 비밀번호를 덮어쓰지 않습니다.
   이미 계정이 등록된 DB에서는 새 SQL을 다시 넣거나 기존 계정을 초기화하지 마세요. 기존 호환 자료를 참조하거나 별도 새 DB를 준비합니다.
   계정 미등록·서명키 쌍 불일치가 있으면 로그인이 실패하며, 서버 기동만으로 로그인 성공을 판단하지 않습니다.

4. 터미널 B의 `BE/`에서 MCP를 시작하고 로그인합니다.

   ```sh
   python3 scripts/run_server.py mcp
   # 다른 env 파일: python3 scripts/run_server.py mcp --env-file configs/mcp.env
   ```

   **http://127.0.0.1:8080/chat.html** 에서 질문을 보내거나 **로그인하기**를 누르고 생성한 사용자 이름·비밀번호를 입력합니다.
   API 인증 후 MCP가 자동 연결됩니다. 로그인 전 질문은 보존되며 연결 후 전송할 수 있습니다. 계정 변경은 **로그아웃** 후 다시 로그인합니다.
   실제 본인 권한 경로·카탈로그·페이지/엔드포인트/필드 접근을 확인하고, 관리는 미리보기와 명시적 실행을 거칩니다.
   “내 역할은 뭐야?”, “R100에 접근할 수 있어?” 등 정해진 질문을 지원하며 LLM은 호출하지 않습니다.
   수동 JWT 연결은 **개발자용 토큰 연결**에 남아 있습니다. API 토큰·개인키를 화면에 넣지 마세요.

5. 각 앱 터미널에서 `Ctrl+C`로 종료하고 DB volume을 보존합니다.

   ```sh
   docker compose --env-file .env stop access-db
   ```

   런처는 foreground 프로세스를 Java로 교체하므로 JVM의 종료 코드·시그널을 그대로 사용합니다. `down -v`, `prune`, 계정·데이터 초기화는 하지 않습니다.

### 서버 env 규칙과 변경 절차

| 항목 | 규칙 |
|---|---|
| env 파일 경로 | 기본 API `.env.api`, MCP `.env.mcp`; `--env-file`의 상대 경로도 **BE 기준**. 루트에서 `python3 BE/scripts/run_server.py api`로 실행해도 동일 |
| 키 파일 경로 | 절대 로컬 경로 또는 **선택한 env 파일의 디렉터리 기준** 상대 경로. 공백 경로는 따옴표 사용. `file:`/HTTP URL·`~`/환경변수 확장은 지원하지 않음 |
| JAR·실행 cwd | 항상 런처 파일의 상위 `BE/`; 두 고정 `*-server/build/libs/*-server-0.1.0.jar`만 사용 |
| 우선순위 | 해당 서버의 허용된 **shell 변수 > 파일 값 > 문서화된 기본값**. 존재하는 빈 shell 값은 오류이며 파일로 돌아가지 않음. 이전 export가 남았다면 해당 변수를 unset한 뒤 실행 |
| 신뢰·설정 | issuer/audience는 표의 고정값만 허용. API profile은 `local`만, MCP profile 설정은 받지 않음. JAR 안의 정해진 properties만 읽고 외부 cwd/config 설정·임의 JVM/Spring 옵션은 전달하지 않음 |

API 파일은 네 DB 값과 `DEMO_PUBLIC_KEY`, `AUTH_PUBLIC_KEY`, `AUTH_PRIVATE_KEY`가 필수입니다. 선택값은 `SERVER_PORT=8081`, `SPRING_PROFILES_ACTIVE=local`,
`DEMO_ISSUER=https://demo-delegation.invalid`, `DEMO_AUDIENCE=access-api`입니다.
MCP 파일은 세 키 경로가 필수이며 선택값은 `SERVER_PORT=8080`, `ACCESS_BASE_URL=http://127.0.0.1:8081`,
`DEMO_ISSUER=https://demo-issuer.invalid`, `DEMO_AUDIENCE=access-mcp`입니다.
포트는 1–65535, DB 이름은 영문·숫자·밑줄 1–64자, 전용 DB 사용자 이름은 같은 문자 1–32자이며 `root`는 금지합니다.
API 주소는 `http://127.0.0.1:<포트>`만 허용합니다. `localhost`, IPv6, 후행 `/`, 사용자정보·query·fragment는 거부합니다.
API 포트를 바꾸면 MCP의 API 주소 포트도 맞추세요. API 로그인 issuer는 `https://demo-issuer.invalid`, 직접 API audience는 `access-api`로 고정되며 `AUTH_ISSUER`·TTL·fixture 계정 설정을 런처 입력으로 받지 않습니다.
API에는 위임 개인키를, MCP에는 사용자 로그인 개인키와 DB 설정을 전달하지 않습니다.

UTF-8 파일 최대 64KiB이며 빈 줄·전체 줄 `#` 주석·`KEY=VALUE`를 지원합니다. key/등호 주변 공백은 무시하고 첫 `=`에서 나눕니다.
따옴표 없는 값은 바깥 공백을 제거하고 **공백 뒤 `#`**부터 주석으로 취급합니다. 값 내부의 추가 `=`·공백·공백 없는 `#`는 보존합니다.
단일/이중 따옴표 안에서는 공백과 `#`를 보존하며 닫는 따옴표 뒤에는 공백·주석만 가능합니다.
같은 종류의 따옴표를 값 내부에 넣는 escape 문법은 없습니다. 다른 종류의 따옴표는 인용된 값 내부에 넣을 수 있습니다.
역슬래시·`$name`·`$(...)`·백틱은 실행/확장하지 않는 데이터입니다. 단, Spring의 재해석을 막기 위해 **`${`가 한 번이라도 있으면 거부**합니다.
이 검사는 shell이 덮어쓸 파일 값에도 적용합니다. `export`, 여러 줄 값, 제어문자, 중복·알 수 없는·다른 서버의 키도 거부합니다.
파일을 `source`하거나 셸 문법으로 실행하지 마세요. Compose dotenv와 완전히 같은 문법이 아니므로 특수문자가 있는 비밀번호는 두 파일에서 같은 실제 값이 되도록 작성하세요.

런처 오류는 범주·허용된 키 이름·줄번호만 표시합니다. root 비밀번호·다른 서버 설정·`DEMO_DIR`·`JAVA_TOOL_OPTIONS`·`_JAVA_OPTIONS`·`JDK_JAVA_OPTIONS`·Spring override 변수는 JVM으로 전달하지 않습니다.
필수 값·파일·Java를 검사한 뒤 실행하고, 비밀번호와 키 내용은 argv에 넣지 않습니다. OS 실행에 필요한 PATH/JAVA_HOME/HOME·locale/temp 변수만 제한적으로 상속합니다.
설정을 바꿀 때 실제 env 파일만 편집하고 해당 앱을 `Ctrl+C` 후 재실행하세요. DB 비밀번호 변경은 기존 MySQL 사용자의 비밀번호를 자동 갱신하지 않으며, 기존 volume을 삭제해서 해결하지 않습니다.

### 기존 직접 실행과 검증

직접 `java -jar ...` 실행은 그대로 지원하며 런처의 env 파일을 자동으로 읽지 않습니다. 직접 실행은 필요한 DB 환경변수와 키 설정을 별도로 준비해야 합니다.
이 경우 두 앱의 `DEMO_DIR` 기본값은 실행 cwd 기준 `.dev/demo`; 다른 디렉터리라면 각 터미널에서 같은 절대 경로로 설정합니다.
fixture 직접 실행은 생성한 `login-accounts.json`의 해시·사용자 매핑을 읽으므로 SQL 등록을 생략하며 MySQL 검증을 대신하지 않습니다.
`DEMO_DIR`는 새 런처에서는 사용하지 않습니다. 직접 Java 실행은 런처의 외부 설정 차단 보장을 제공하지 않습니다.

```sh
# BE/에서 실행, 직접 실행용 환경변수가 이미 준비된 경우
java -jar api-server/build/libs/api-server-0.1.0.jar --spring.profiles.active=local
java -jar mcp-server/build/libs/mcp-server-0.1.0.jar
# Docker 없는 개발 검증 전용; MySQL 검증이 아님
java -jar api-server/build/libs/api-server-0.1.0.jar --spring.profiles.active=fixture
# 새 런처 단위 검증과 기존 HTTP/MCP 통합 검증
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p test_run_server.py -v
./gradlew verify
```

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
기존 12개와 페이지 맥락 조회 4개를 합친 16개 Tool 모두 추가 인수를 거부합니다. `entity`는 users/roles/policies/resources, 일반 대상 ID는 `[A-Za-z0-9_-]{1,64}`입니다.
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

## Page · Endpoint · Field 정책

백오피스 접근 대상을 세 가지 유형으로 등록하고, 사용자의 역할에 부여된 정책으로 **같은 페이지 맥락**의 접근을 평가합니다.

| 정의 테이블 | 저장하는 내용 |
|---|---|
| `access_pages` | 어드민 application, 페이지 path, 이름 |
| `access_endpoints` | service, HTTP method, API path, 이름 |
| `access_fields` | 소속 endpoint, REQUEST/RESPONSE, PATH/QUERY/HEADER/BODY 위치, 필드 경로 |
| `page_endpoints` | 페이지에서 사용하는 엔드포인트 관계. 등록 자체는 권한이 아님 |
| `context_policy_rules` | 기존 policy의 PAGE/ENDPOINT/FIELD 유형과 정확한 page/endpoint/field 대상 |

정책은 기존 `policies`에 등록하고 기존 `role_policies`로 역할에 부여합니다. 한 정책은 하나의 유형과 대상을 가지며 역할에 여러 정책을 묶을 수 있습니다.
PAGE는 해당 페이지 허용이 필요합니다. ENDPOINT는 페이지 허용, 사용 관계 등록, 해당 페이지·엔드포인트 정책이 모두 필요합니다.
FIELD는 여기에 해당 페이지·엔드포인트·필드 정책이 추가로 필요합니다. 다른 페이지의 정책, 요청 필드의 허용, 상위 리소스 허용이 응답 필드 허용으로 전파되지 않습니다.
사용자가 가진 여러 역할의 허용은 각 단계에서 합쳐 평가하며, 근거는 실제 역할·정책 ID로 반환합니다. 관리자 역할만으로 업무 페이지 접근을 우회하지 않습니다.

기존 `resources`/`policy_resources`의 `R###` 계약은 유지됩니다. 새로운 유형의 ID는 그 목록에 섞이지 않으며 기존 리소스 허용도 유형별 정책 평가에 사용되지 않습니다.
유형별 정책에는 일반 `policy_resources`를 함께 설정하지 마세요. 기존 `attach_resource_to_policy`/`detach_resource_from_policy`는 유형별 정책을 `INVALID_ARGUMENT`로 거부합니다.
기존 역할 부여·회수와 역할–정책 추가·해제 도구는 유형별 정책에도 적용되며, 다음 조회부터 실제 권한이 달라집니다. MySQL에서는 기존 트랜잭션·감사 기록을 그대로 사용합니다.

| MCP 도구 | API GET | 인수 |
|---|---|---|
| `list_my_pages` | `/api/me/pages` | 선택: `after`, `limit` |
| `list_my_page_endpoints` | `/api/me/pages/{pageId}/endpoints` | `pageId`; 선택: `after`, `limit` |
| `list_my_endpoint_fields` | `/api/me/pages/{pageId}/endpoints/{endpointId}/fields` | `pageId`, `endpointId`, `direction` (`REQUEST`/`RESPONSE`); 선택: `after`, `limit` |
| `check_my_context_access` | `/api/me/context-access` | `targetType`, `pageId`; ENDPOINT/FIELD는 `endpointId`, FIELD는 `fieldId`도 필수 |

모두 `authz.read`/`access.read`가 필요한 본인 조회입니다. 인증 주체를 입력으로 받지 않습니다.
목록은 ID 순서로 최대 5개를 반환하고, 다음 페이지가 있을 때 `nextCursor`를 제공합니다. 다음 요청의 `after`에 그 값을 넣습니다.
부모 페이지나 엔드포인트를 볼 수 없으면 존재 여부와 관계없이 동일 `404 NOT_FOUND`입니다. 볼 수 있는 부모에 허용된 하위 항목이 없으면 빈 목록을 반환합니다.
API의 알 수 없는·중복 query parameter, 도구의 추가 인수, 잘못된 ID·타입·조건 조합은 거부합니다.

접근 확인은 `allowed`, `reason`, `stages`를 반환합니다. `reason`은 `ALLOWED`, `PAGE_NOT_GRANTED`, `ENDPOINT_NOT_GRANTED`, `FIELD_NOT_GRANTED`이며 첫 거부 단계에서 평가 결과 설명을 끝냅니다.
이 응답으로 접근 확인, 허용 근거 설명, 부족한 단계 확인을 함께 수행합니다. 거부된 대상의 이름·경로·숨겨진 정책은 공개하지 않습니다.
각 성공 단계의 `grants`에는 역할·정책 근거를 최대 5개 제공하고, 그 이상은 `grantsTruncated:true`로 표시합니다. 실제 판정은 모든 해당 근거를 사용합니다.
등록 정보는 ID 64자, 경로 200 ASCII 문자, 이름 80 Java 문자 이하로 제한하며 목록/근거 제한과 함께 downstream 16KiB 응답 제한을 지킵니다.

예를 들어 Alice는 `page-customers`와 `page-audit`에서 같은 `endpoint-customer-get`을 호출할 수 있습니다.
고객 페이지에서는 응답 `name`만 허용되고, 감사 페이지에서는 `name`과 `email`이 허용됩니다. 감사 페이지의 PAGE/ENDPOINT 정책은 `role-editor`, `email` FIELD 정책은 `role-analyst`에서 부여되어 역할 합성을 검증합니다.
Bob, Carol, `local-id-demo`, `admin`은 초기 상태에서 유형별 업무 페이지 권한이 없습니다. `fixture`와 `local` 데이터는 같은 예제를 제공합니다.

```json
{"name":"list_my_pages","arguments":{"limit":5}}
{"name":"list_my_endpoint_fields","arguments":{"pageId":"page-audit","endpointId":"endpoint-customer-get","direction":"RESPONSE"}}
{"name":"check_my_context_access","arguments":{"targetType":"FIELD","pageId":"page-customers","endpointId":"endpoint-customer-get","fieldId":"field-customer-email-response"}}
```

위 객체를 기존 `tools/call` 요청의 `params`로 사용합니다. 마지막 예제는 `FIELD_NOT_GRANTED`입니다. `pageId`만 `page-audit`로 바꾸면 허용되며 세 단계의 근거를 받습니다.
현재 HTML 채팅 UI는 기존 12개 도구 흐름을 유지합니다. 추가 도구는 MCP 클라이언트 또는 직접 MCP 요청으로 사용합니다.

### 정의 등록과 정책 부여

V5는 스키마만 추가하고 V6는 `local` 프로필의 예제 데이터만 추가합니다. 이미 적용한 V1–V4를 수정하거나 기존 데이터를 초기화하지 않습니다.
운영 정의는 별도 migration/관리용 DB 계정으로 아래와 같은 **검토된 명시적 SQL 트랜잭션**을 적용합니다. 임의 SQL을 실행하는 API/MCP 도구는 없습니다.
새로운 정의는 아직 사용자에게 권한을 주지 않습니다. FK는 페이지 사용 관계와 필드의 엔드포인트 소속을 검사하고 CHECK는 유형별 대상 조합·메타데이터 제한을 검사합니다.

```sql
START TRANSACTION;
INSERT INTO access_pages (id,application,path,name)
VALUES ('page-orders','backoffice','/admin/orders','Orders');
INSERT INTO access_endpoints (id,service,method,path,name)
VALUES ('endpoint-orders-get','order-api','GET','/orders/{id}','Get order');
INSERT INTO access_fields (id,endpoint_id,direction,location,field_path,name)
VALUES ('field-order-number-response','endpoint-orders-get','RESPONSE','BODY','number','Order number');
INSERT INTO page_endpoints (page_id,endpoint_id)
VALUES ('page-orders','endpoint-orders-get');
INSERT INTO policies (id,name,description,protected) VALUES
('ctx-page-orders','Orders page','Orders page access',FALSE),
('ctx-endpoint-orders','Orders endpoint','Orders page API access',FALSE),
('ctx-field-order-number','Order number','Order number output access',FALSE);
INSERT INTO context_policy_rules (policy_id,kind,page_id,endpoint_id,field_id) VALUES
('ctx-page-orders','PAGE','page-orders',NULL,NULL),
('ctx-endpoint-orders','ENDPOINT','page-orders','endpoint-orders-get',NULL),
('ctx-field-order-number','FIELD','page-orders','endpoint-orders-get','field-order-number-response');
COMMIT;
```

이후 권한 있는 관리자가 기존 `attach_policy_to_role` 도구로 필요한 정책 각각을 실제 업무 역할에 부여합니다.
예: `{"roleId":"role-analyst","policyId":"ctx-page-orders"}`를 부여한 뒤 엔드포인트·필드 정책도 각각 부여해야 주문 번호를 조회할 수 있습니다.
정의를 바꾸는 migration에서도 기존 policy를 `policy_resources`와 유형별 규칙 양쪽에 겸용하지 않고, 기존 참조 관계와 영향 사용자를 검토합니다.

이 서버는 권한 정의·판정·설명을 제공합니다. 실제 업무 API 트래픽 차단, 요청 본문 검사, 응답 필드 삭제/마스킹을 자동 수행하지 않습니다.
백오피스/API에서 이 판정 결과를 적용하는 별도 연결이 필요합니다. DENY·조건·만료 정책, 승인 워크플로, 전체 변경 영향 시뮬레이션과 정의 편집 UI는 포함하지 않습니다.

## API 로그인과 인증 경로

브라우저는 같은 출처의 `POST /auth/login`에 자격 증명만 보냅니다. MCP는 고정 `POST /api/auth/login`으로 전달하고 **API만** 비밀번호를 검사하고 사용자 토큰을 발급합니다.
MCP 로그인 relay는 수신한 Authorization·Origin·사용자 헤더를 전달하지 않고 redirect를 따르지 않습니다. 두 로그인 POST만 익명이며 API 업무 경로와 MCP 도구 16개는 계속 인증이 필요합니다.

요청은 `application/json`의 정확한 `{"username":"alice","password":"..."}`입니다. username은 대소문자를 구분하는 ASCII `[A-Za-z0-9_-]{1,64}`, password는 변형하지 않는 1–256 Java 문자입니다.
추가·중복 JSON 필드, 잘못된 타입, query parameter와 4KiB 초과 본문을 거부합니다. chunked 전송과 인코딩된 로그인 경로에도 같은 본문 제한을 적용합니다.

| 응답 | 내용 |
|---|---|
| 200 | `tokenType:"Bearer"`, `expiresIn`, `mcpAccessToken`, `apiAccessToken`, `user:{id,name}` |
| 400 | `{"code":"INVALID_ARGUMENT"}` |
| 401 | `{"code":"INVALID_CREDENTIALS"}` — 없는 계정·비활성 계정·잘못된 비밀번호 모두 동일 |
| 503 | `{"code":"AUTH_UNAVAILABLE"}` — 미등록 fixture 자료·서명키·DB·upstream 실패 등 |

모든 로그인 응답은 `Cache-Control: no-store`이며 성공 본문은 16KiB 이하입니다. 쿠키를 만들지 않습니다. 기존 Host/Origin 위반은 403입니다.
`expiresIn`은 두 토큰의 실제 `exp-iat`와 같은 초 단위 수명입니다. `auth.token-ttl-seconds` 기본값은 900, 허용 범위는 60–900입니다.
UI는 MCP 토큰과 만료 시간만 메모리에 유지하고 API 토큰을 앱 상태에 보관하지 않습니다. 로그아웃·만료·401은 토큰과 이전 사용자 결과를 지우며 다시 로그인하도록 안내합니다.

`login_accounts`는 대소문자를 정확히 구분하는 `username VARBINARY(64)` → 고유 `user_id` 매핑, `password_hash`, `enabled`를 저장합니다.
해시는 `pbkdf2-sha256$600000$<base64url 16바이트 salt>$<base64url 32바이트 hash>`입니다. JDK PBKDF2WithHmacSHA256과 임의 salt, 고정 반복 횟수, 상수 시간 비교를 사용합니다.
없는 계정도 유효한 dummy 해시로 같은 비용의 검증을 한 번 수행하고 비활성 계정도 비밀번호 검증을 생략하지 않습니다.
fixture manifest는 정확한 `{username,userId,issuer,subject,passwordHash,enabled}` 객체 배열이며 `enabled:false`를 지원합니다. 실제 fixture 사용자와 매핑이 다르면 로그인은 실패합니다.
`local-id-demo` 로그인은 JWT subject `local-id-demo`, 로컬 사용자 ID `U900`으로 연결됩니다. username·subject·로컬 ID가 같다고 가정하지 않습니다.

| API 설정 | 기본값 |
|---|---|
| `auth.issuer` | `https://demo-issuer.invalid` |
| `auth.public-key` | `file:${DEMO_DIR:.dev/demo}/issuer-public.pem` |
| `auth.private-key` | `file:${DEMO_DIR:.dev/demo}/issuer-private.pem` |
| `auth.fixture-accounts` | `file:${DEMO_DIR:.dev/demo}/login-accounts.json` |
| `auth.token-ttl-seconds` | `900` |

로그인 자료가 없으면 로그인만 실패하며 이미 구성된 공개키로 검증 가능한 기존 Bearer 경로를 인메모리 인증으로 대체하지 않습니다.
API는 로그인 시점의 보호된 `SYSTEM_ADMIN` 역할을 조회해 관리자에게만 manage scope를 발급합니다. 발급 후에도 실제 관계 변경마다 현재 DB 관리자 여부를 다시 검사합니다.
역할을 회수하면 남은 manage scope로 변경할 수 없으며, 새 관리 권한을 받으면 다시 로그인해 manage scope를 받아야 합니다.

### 서로 분리된 서명과 검증

| 구간 | 인증 및 권한 |
|---|---|
| 클라이언트 → MCP | RS256·고정 공개키, issuer `https://demo-issuer.invalid`, audience `access-mcp`, 필수 exp/문자열 nonblank sub, 선택 nbf; HTTP `mcp.connect`, 읽기 10개 `authz.read` / 쓰기 6개 `authz.manage` |
| 클라이언트 → API 직접 호출 | 같은 사용자 issuer·공개키, audience `access-api`, `access.read` / `access.manage`, iat/exp와 최대 900초 수명; `act`·`original_iss` 금지 |
| MCP → API | 별도 키로 서명한 JWT, issuer `https://demo-delegation.invalid`, audience `access-api`, 고정 `act.sub=access-mcp`, 검증된 `original_iss`, 동작별 고정 scope `access.read` 또는 `access.manage`, 검증된 사용자 sub |

JWT의 단일 `aud`는 [RFC 7519 §4.1.3](https://www.rfc-editor.org/rfc/rfc7519#section-4.1.3)에 따라 문자열 또는 한 항목 배열로 표현할 수 있습니다. 로그인 발급기는 문자열을 사용하며 검증기는 같은 audience 값으로 해석합니다.
MCP는 Spring Security가 검증한 issuer+subject만 사용합니다. 들어온 Bearer와 임의 act/original_iss/scope/grants를 downstream으로 복사하지 않습니다.
API 위임 JWT의 만료는 `min(현재+60초, 원래 사용자 토큰의 exp)`입니다. API도 서명·issuer·audience·시간·actor·원issuer·주체 형식과 최대 60초 수명을 확인합니다.
API는 두 고정 검증기를 사용하며 검증 전 iss는 검증기 선택에만 사용합니다. 각 검증기가 자신의 공개키·issuer·audience·필수 claim을 독립 검증하고 다른 키로 재시도하지 않습니다.
`VerifiedCaller`는 Spring이 검증한 JWT에서 직접 호출의 iss+sub 또는 위임 호출의 original_iss+sub를 해석합니다. API 컨트롤러 세 곳 모두 같은 해석을 사용합니다.
클라이언트 scope는 Tool/API 사용 허가일 뿐 **DB의 도메인 권한이 아닙니다**. API는 검증된 사용자 issuer+subject로 사용자를 찾고 본인 역할에서만 경로를 계산합니다.
DB identity는 VARBINARY로 정확히 비교하며 trim/lowercase/유니코드 정규화를 하지 않습니다. `X-User-Id`와 UI 선택값은 권한 근거가 아닙니다.

**위임 개인키를 가진 MCP는 사용자 신원을 주장할 수 있는 신뢰 주체입니다.** API는 원래 사용자 JWT 대신 MCP의 별도 서명 주장을 검증합니다.
이는 로컬 데모 위임이며 정식 OAuth Token Exchange/OBO 구현이 아닙니다. 유효기간 안의 재사용 방지/폐기 목록과 자동 키 회전은 구현하지 않았습니다.
API만 사용자 서명용 `issuer-private.pem`을 필요로 하고, MCP는 사용자 공개키와 별도의 `delegation-private.pem`을 필요로 합니다.
`--fixtures`는 같은 키로 검증 JWT를 갱신하기 위한 `fixture-issuer-private.pem`도 저장합니다. 모든 개인키와 로그인 자료는 backend-only 파일이며 FE·JAR·로그에 포함하지 않습니다.
테스트 fixture만 갱신할 때는 `java tools/DemoCredentials.java <기존fixture디렉터리> --refresh-fixtures`를 사용합니다. 공개키·개인키·비밀번호·계정 해시를 보존하고 JWT 파일만 원자적으로 교체합니다.
관리 scope는 읽기를 포함하지 않습니다. 관리자 manage-only는 API/Tool 쓰기만, read-only는 전체 카탈로그 조회만 가능합니다.
UI 관리 미리보기는 인증된 주체를 `get_my_access`로 확인하므로 읽기+쓰기 scope가 필요하며, 전송 후 취소/통신 단절은 rollback을 뜻하지 않습니다.

전송은 **WebMVC + SYNC + stateless Streamable HTTP**(`protocol=STATELESS`)입니다. MCP 세션/로그인 쿠키 없이 요청마다 인증합니다.
Servlet 동기 경로가 주입된 Spring 보안 프록시를 호출합니다. 단절 시 이미 시작한 읽기는 즉시 취소가 보장되지 않고 downstream 제한 시간까지 남을 수 있습니다.
로컬 사용자 이름·비밀번호 로그인, 직접 Bearer HTTP와 같은 출처 UI를 지원합니다. 로그아웃은 브라우저 상태만 지우며 이미 발급된 토큰을 서버에서 폐기하지 않습니다.
SSO·MFA·OAuth 발견/인증 흐름·보호 자원 메타데이터·refresh token·폐기 목록·비밀번호 복구는 구현하지 않았습니다.

## 안전성 및 데이터 초기화

| 항목 | 구현 |
|---|---|
| HTTP 요청 | loopback 바인딩, MCP POST 본문 16KiB·로그인 4KiB 제한(chunked/인코딩 경로 포함), SDK 오류 원문 정제 |
| downstream | 고정 목적지/경로·GET/PUT/DELETE 및 로그인 전용 POST, redirect 금지, 연결 1초·헤더/본문 2초, 응답 16KiB 제한·DTO 관계 검증 |
| 브라우저 | GET `/chat.html`, `/chat.js`, `/chat.css` 및 POST `/auth/login` 공개, 정확한 같은 Origin의 `/mcp`·`/auth/login` POST 허용, API는 모든 Origin 거부, CSP self |
| 오류/로그 | Tool `isError=true` + 고정 코드, 내부 예외·토큰·추가 필드 미노출, 원문을 기록하는 SDK logger 3곳 비활성화 |
| DB | parameter binding, FK/복합 PK·identity unique/index, Flyway clean 비활성화, schema와 local seed 경로 분리 |

정상 기동 후 같은 volume에 API를 다시 시작해도 Flyway history로 중복 seed를 넣지 않습니다.
`data.sql`, 컨트롤러 초기 데이터 삽입, truncate/reseed는 없습니다. 기본/비local 프로필은 스키마만 읽고 local seed를 스캔하지 않습니다.
개발 DB 계정은 전용 DB 안에서 migration·조회·관계 변경·감사 기록을 수행합니다. 운영에서는 migration 계정을 분리하고 앱 계정에는 필요한 테이블의 조회·관계 변경·감사 INSERT 권한만 부여하세요.

HTTP 인증 실패는 401, 진입 권한 부족은 403입니다. Tool 오류는 `FORBIDDEN`, `INVALID_ARGUMENT`, `UPSTREAM_ACCESS_DENIED`,
`UPSTREAM_UNAVAILABLE`, `UPSTREAM_INVALID_RESPONSE`, `NOT_FOUND`, `INTERNAL_ERROR` 중 하나입니다. 리소스 접근 거부는 정상 boolean 결과입니다.

## 호출과 검증

아래는 `BE/`에서 실행합니다. 로그인과 직접 API 호출은 다음처럼 같은 프로세스 메모리에서 연결할 수 있습니다. 비밀번호와 두 토큰을 출력하거나 URL·명령 인수에 넣지 않습니다.

```sh
python3 - <<'PY'
import json, os, urllib.request
from pathlib import Path
directory = Path('.dev/demo-login')  # 위 실행 절차에서 선택한 자료 디렉터리
passwords = json.loads((directory / 'login-passwords.json').read_text())
request = urllib.request.Request('http://127.0.0.1:8081/api/auth/login',
    data=json.dumps({'username': 'alice', 'password': passwords['alice']}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(request) as response:
    session = json.load(response)
request = urllib.request.Request('http://127.0.0.1:8081/api/me',
    headers={'Authorization': 'Bearer ' + session['apiAccessToken']})
with urllib.request.urlopen(request) as response:
    access = json.load(response)
print(json.dumps({'userId': access['user']['id'],
                  'resources': [item['id'] for item in access['resources']]}))
PY
```

기존 개발용 MCP fixture 토큰으로도 호출할 수 있습니다. 토큰은 요청 헤더에만 넣고 출력하지 않습니다.

```sh
MCP_TOKEN="$(cat .dev/demo-login/mcp-alice.jwt)"
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
| `./gradlew build` | Java 컴파일·JUnit·실행 JAR·의존성 해결 |
| `./gradlew verify` | 명시 fixture 프로필, 실제 JVM·HTTP·MCP·JWT·메서드 권한·위임·동시 사용자 격리·오류/타임아웃·로그 |
| `python3 scripts/verify_mysql.py` | Docker DB build/up + local 프로필 통합 검증 2회 + Flyway checksum/history·seed 수 동일성, volume 보존 |
| 루트의 `node --test FE/tests/chat.test.mjs` | FE 단위 검증(실브라우저 검증과 별개) |

추가 Page/Endpoint/Field 검증은 빌드된 JAR와 PATH의 JDK 21을 사용합니다. 아래는 `BE/` 기준입니다.

```sh
python3 tests/e2e/verify_context_access.py --profile fixture
python3 tests/e2e/verify_context_access.py --profile local \
  --mysql-home /absolute/path/to/extracted/mysql \
  --mysql-library-path /absolute/path/to/needed/libs
```

두 번째 명령은 개발 DB를 사용하지 않고 소유한 임시 디렉터리·loopback 포트·전용 앱 계정으로 격리 MySQL을 실행합니다.
필요한 공유 라이브러리가 이미 검색 경로에 있으면 `--mysql-library-path`는 생략할 수 있습니다. 실행 후 자신이 만든 서버·임시 데이터만 정리하며 Docker/기존 volume을 변경하지 않습니다.
상세 결과는 `BE/test-results/<timestamp>-context-<uuid>/report.json`에 기록됩니다. fixture 결과는 MySQL 검증을 대신하지 않습니다.

로그인 전용 통합 검증도 실제 API/MCP JAR를 실행합니다. 기본 fixture 실행과 기존 Playwright/Chromium을 사용하는 격리 MySQL·브라우저 실행은 다음과 같습니다.

```sh
python3 tests/e2e/verify_auth.py --profile fixture
python3 tests/e2e/verify_auth.py --profile local \
  --mysql-home /absolute/path/to/extracted/mysql \
  --mysql-library-path /absolute/path/to/needed/libs \
  --browser --playwright-module /absolute/path/to/existing/playwright/index.mjs \
  --chromium /absolute/path/to/chromium
```

로그인 검증은 새 임시 자격 증명을 생성하고 V7 이후 해시 SQL을 명시 등록합니다. 실제 로그인, 두 audience, 기존 위임, 권한 회수,
입력·신뢰 경계·오류·비밀정보 비노출을 검사하며 브라우저 선택 시 로그인→채팅→로그아웃→계정 전환을 실제 HTTP로 확인합니다.
기존 개발 DB나 계정을 재사용하지 않습니다. 브라우저를 요청하지 않은 실행은 브라우저 검증 통과를 의미하지 않습니다.

### 원격 로그인 변경의 검증 기록(런처 병합 전) — 2026-11-14

다음은 원격 로그인 커밋에서 기록한 검증이며, 새 env 런처와 합친 버전의 검증 결과를 뜻하지 않습니다.

| 직접 실행한 검증 | 결과 |
|---|---|
| Gradle build와 JUnit | 51 PASS — API 19개, MCP 23개, 공통 보안 9개 |
| 기존 실제 fixture JVM·HTTP·MCP 통합 검증 | 17 PASS |
| Page/Endpoint/Field 회귀 | fixture 8 PASS + MySQL 전용 1 SKIPPED; 실제 MySQL 9 PASS |
| 로그인 fixture 통합·실제 로그인 브라우저 | 10 PASS + MySQL 전용 3 SKIPPED |
| 실제 MySQL 로그인·직접 API·위임·로그인 브라우저·로그 | 12 PASS |
| 기존 실제 브라우저 회귀 | 9개 범위 확인 — 이전 실행 8개 + 별도 읽기 재실행 1개 |
| Node 단위 테스트 | 83 PASS — FE 80개, 브라우저 검증 보조 계약 3개 |
| Python 테스트 실행기 단위 테스트 | 11 PASS |

실제 DB 검증은 격리된 네이티브 MySQL 8.4.13을, 브라우저는 Chromium Headless Shell 151.0.7922.34를 사용했습니다.
V7의 계정 자동 등록 없음, 생성 해시의 명시 등록, 잘못된·없는·비활성 계정, 두 audience와 서명키 분리, 직접/위임 사용자 매핑,
관리자 권한 회수, 같은 DB 재시작, 비밀정보 비노출과 로그인→MCP 초기화→채팅→로그아웃→다른 계정 로그인을 확인했습니다.
로그인 성공과 업무 응답은 실제 HTTP로 확인했으며 브라우저 응답을 가짜 성공으로 대체하지 않았습니다.

최초 실행에서는 기존 읽기 브라우저 검사가 실패했으며, 별도 새 DB 실행에서 읽기 25개 확인과 원문 로그 검사, 총 2개 선택 테스트가 통과했습니다.
이 재실행에서 나머지 11개 테스트는 선택하지 않았습니다. 이전 실패 보고서도 덮어쓰지 않고 보존했습니다.
여기에는 R400/R999 거부 결과의 `allowed:false`, `resource:null`, 빈 grants와 거부 UI가 포함됩니다.
네이티브 로그인 12개와 기존 브라우저 9개 범위는 이 재실행을 포함한 결과이며 단일 13/13 실행으로 집계하지 않습니다.
이번 검증에서 Docker 경로와 기존 관리 시나리오 전체 38개를 다시 실행하지는 않았습니다. 상세 실행 결과는 로컬 검증 산출물로 보존하며 Git에 포함하지 않습니다.

### 이전 Page/Endpoint/Field 변경의 검증 — 2026-11-14

| 직접 실행한 검증 | 결과 |
|---|---|
| Gradle build와 JUnit | 30 PASS — API 11개, MCP 19개(기존 2개 포함) |
| 기존 실제 fixture JVM·HTTP·MCP 통합 검증 | 17 PASS |
| 새 맥락 권한 fixture API·MCP 검증 | 8 PASS, MySQL 전용 검사 1 SKIPPED; FAIL/BLOCKED 0 |
| 격리된 실제 MySQL 8.4.13 API·MCP 검증 | 9 PASS; FAIL/BLOCKED/SKIPPED 0 |
| FE·브라우저 검증 보조 로직 Node 단위 테스트 | 67 PASS |
| Python 테스트 실행기 단위 테스트 | 11 PASS |

실제 MySQL 검증은 네이티브 바이너리와 새 임시 DB를 사용했습니다. Flyway V1–V6, JDBC/API/MCP 결과 일치,
기존 감사 트랜잭션을 통한 정책 변경의 즉시 반영, FK·유형 조합·공백/개행/기본 무시 Unicode 문자 제약,
동일 DB에서의 API 재시작과 데이터 보존을 확인했습니다. Docker 경로와 기존 v2 관리 시나리오 전체 38개는 이번 실행 범위에 포함하지 않았습니다.
재실행 시 위 명령과 `node --test FE/tests/chat.test.mjs BE/tests/e2e/browser_contract.test.mjs`,
`python3 -m unittest discover -s BE/tests/e2e -p test_runner.py`를 사용합니다(마지막 두 명령은 저장소 루트 기준).
상세 실행 결과 디렉터리는 로컬 검증 산출물이며 Git에 포함하지 않습니다.

### 이전 v2 개발 검증 기록

아래는 기존 README에 남아 있던 이전 실행 기록입니다. 상세 `BE/test-results/` 파일은 Git에서 제외되어 있으므로 새 체크아웃에서는 위 명령으로 다시 검증합니다.

v2 개발 검증은 17개 통합 테스트(실제 fixture 프로필 JVM·HTTP)와 DTO 검증 JUnit 2개가 통과했습니다. 12개 Tool·관리 상태·scope 조합 회귀를 포함하며 최신 결과 파일에서 재확인할 수 있습니다.
HTTP 통합 결과는 `BE/build/integration/results.txt`, MySQL 전용 결과는 `BE/build/mysql-verification/`입니다.
핵심 JWT와 API/MCP 경로는 mock으로 우회하지 않습니다. 지연·redirect·잘못된 응답·outbound 관찰에만 테스트 HTTP 서버를 사용합니다.
실제 v2 MySQL 변경·감사·동시성·기존 관리 UI의 과거 기록은 핵심 38개 고유 시나리오의 여러 실행에 걸친 누적 PASS이며 단일 실행 38 PASS는 아닙니다. 과거 v1·인메모리 결과는 이 MySQL 검증의 근거로 사용하지 않습니다.
당시 UI 패키지의 별도 실제 검증은 9 PASS였습니다. 범위는 새 UI 6개(안내·관계·카탈로그·관리·계정 전환·모바일)와 기존 관리 브라우저·결과 미확인 상태·로그 비밀정보 검사 3개입니다. 이 기록도 이번 로그인 변경 또는 핵심 38개 전체의 재실행을 의미하지 않습니다.
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
DB TLS와 최소 권한 앱 계정, 필요 시 외부 인증 서버·MCP OAuth 흐름 및 트래픽 제한을 별도로 적용해야 합니다.
원래 사용자 산출물 `spring-boot-mcp-guide.md`는 보존했으며 현재 계약은 이 README와 실행 코드가 기준입니다.

다음 행동: `cd BE && ./gradlew verify`를 실행하세요.
