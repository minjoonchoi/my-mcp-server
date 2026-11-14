package example.security;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import static org.assertj.core.api.Assertions.*;

class LoginProtocolTest {
    private static byte[] bytes(String value) { return value.getBytes(StandardCharsets.UTF_8); }
    private static final String RESULT = "{\"tokenType\":\"Bearer\",\"expiresIn\":900,\"mcpAccessToken\":\"a.b.c\",\"apiAccessToken\":\"d.e.f\",\"user\":{\"id\":\"U900\",\"name\":\"사용자\"}}";

    @Test void passwordIsExactAndUsernameCaseIsPreserved() {
        var input = LoginProtocol.credentials(bytes("{\"username\":\"Alice\",\"password\":\"  Päß  \"}"));
        assertThat(input.username()).isEqualTo("Alice");
        assertThat(input.password()).isEqualTo("  Päß  ");
        assertThat(input.toString()).doesNotContain("Päß");
    }

    @Test void rejectsDuplicateUnknownTypedTrailingAndOutOfRangeInput() {
        for (String invalid : new String[] {"null", "[]", "{}", "{\"username\":\"alice\",\"password\":\"x\",\"role\":\"admin\"}",
                "{\"username\":\"alice\",\"password\":\"x\",\"password\":\"x\"}", "{\"username\":42,\"password\":\"x\"}",
                "{\"username\":\"alice\",\"password\":true}", "{\"username\":\"álice\",\"password\":\"x\"}",
                "{\"username\":\"alice \",\"password\":\"x\"}", "{\"username\":\"alice\",\"password\":\"\"}",
                "{\"username\":\"alice\",\"password\":\"" + "x".repeat(257) + "\"}",
                "{\"username\":\"alice\",\"password\":\"x\"} {}"}) {
            assertThatThrownBy(() -> LoginProtocol.credentials(bytes(invalid))).isInstanceOf(LoginProtocol.InvalidRequest.class).hasMessage("INVALID_ARGUMENT");
        }
        String valid = "{\"username\":\"alice\",\"password\":\"x\"}";
        assertThat(LoginProtocol.credentials(bytes(valid + " ".repeat(4096 - valid.length()))).username()).isEqualTo("alice");
        assertThatThrownBy(() -> LoginProtocol.credentials(bytes(valid + " ".repeat(4097 - valid.length())))).isInstanceOf(LoginProtocol.InvalidRequest.class);
    }

    @Test void resultRequiresExactBoundedDtoAndActualLifetimeRange() {
        assertThat(LoginProtocol.result(bytes(RESULT)).user().id()).isEqualTo("U900");
        assertThat(LoginProtocol.result(bytes(RESULT.replace("900", "60"))).expiresIn()).isEqualTo(60);
        for (String invalid : new String[] {RESULT.replace("900", "59"), RESULT.replace("900", "901"), RESULT.replace("900", "\"900\""),
                RESULT.replace("900", "900.0"), RESULT.replace("\"Bearer\"", "\"Bearer\",\"tokenType\":\"Bearer\""),
                RESULT.replace("\"id\":\"U900\"", "\"id\":\"U900\",\"password\":\"secret\""), RESULT.replace("a.b.c", "bad token"), RESULT + "{}"}) {
            assertThatThrownBy(() -> LoginProtocol.result(bytes(invalid))).isInstanceOf(LoginProtocol.InvalidRequest.class);
        }
        byte[] body = bytes(RESULT);
        assertThat(LoginProtocol.result(bytes(RESULT + " ".repeat(16384 - body.length))).expiresIn()).isEqualTo(900);
        assertThatThrownBy(() -> LoginProtocol.result(bytes(RESULT + " ".repeat(16385 - body.length)))).isInstanceOf(LoginProtocol.InvalidRequest.class);
    }

    @Test void boundaryChecksActualBytesForEncodedLoginAndChunkedLength() throws Exception {
        var request = new MockHttpServletRequest("POST", "/auth/%6cogin");
        request.setContentType("application/json");
        request.setContent(new byte[4097]);
        assertThat(LoginProtocol.isLoginPath(request, false)).isTrue();
        assertThat(LoginProtocol.isLoginPath(request, true)).isFalse();
        var chunked = new jakarta.servlet.http.HttpServletRequestWrapper(request) {
            @Override public long getContentLengthLong() { return -1; }
        };
        var response = new MockHttpServletResponse();
        LoginRequestBoundary.filter(chunked, response, (req, res) -> { throw new AssertionError("Oversize body passed boundary"); });
        assertThat(response.getStatus()).isEqualTo(400);
        assertThat(response.getContentAsString()).isEqualTo("{\"code\":\"INVALID_ARGUMENT\"}");
        assertThat(response.getHeader("Cache-Control")).isEqualTo("no-store");
    }

    @Test void boundaryRejectsWrongContentTypeAndPreservesValidBody() throws Exception {
        var request = new MockHttpServletRequest("POST", "/api/auth/login");
        request.setContentType("text/plain");
        var response = new MockHttpServletResponse();
        LoginRequestBoundary.filter(request, response, (req, res) -> { throw new AssertionError(); });
        assertThat(response.getStatus()).isEqualTo(400);
        request.setContentType("application/json; charset=UTF-8");
        byte[] body = bytes("{\"username\":\"alice\",\"password\":\"x\"}");
        request.setContent(body);
        LoginRequestBoundary.filter(request, new MockHttpServletResponse(), (req, res) -> assertThat(req.getInputStream().readAllBytes()).isEqualTo(body));
    }
}
