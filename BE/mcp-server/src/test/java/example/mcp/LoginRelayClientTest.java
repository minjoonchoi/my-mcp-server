package example.mcp;

import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import example.security.LoginProtocol;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.*;

class LoginRelayClientTest {
    private HttpServer server;
    private LoginRelayClient client;
    private static final String VALID = "{\"tokenType\":\"Bearer\",\"expiresIn\":900,\"mcpAccessToken\":\"a.b.c\",\"apiAccessToken\":\"d.e.f\",\"user\":{\"id\":\"alice\",\"name\":\"Alice\"}}";
    private final AtomicReference<String> body = new AtomicReference<>(VALID);
    private final AtomicReference<String> contentType = new AtomicReference<>("application/json");
    private final AtomicReference<String> target = new AtomicReference<>(), requestBody = new AtomicReference<>();
    private final AtomicReference<com.sun.net.httpserver.Headers> headers = new AtomicReference<>();
    private final AtomicInteger status = new AtomicInteger(200), requests = new AtomicInteger();

    @BeforeEach void start() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            requests.incrementAndGet(); headers.set(exchange.getRequestHeaders());
            target.set(exchange.getRequestMethod() + " " + exchange.getRequestURI());
            requestBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] bytes = body.get().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", contentType.get());
            if (status.get() == 307) exchange.getResponseHeaders().set("Location", "/redirected");
            exchange.sendResponseHeaders(status.get(), bytes.length); exchange.getResponseBody().write(bytes); exchange.close();
        });
        server.start(); client = new LoginRelayClient(URI.create("http://127.0.0.1:" + server.getAddress().getPort()));
    }
    @AfterEach void stop() { if (server != null) server.stop(0); }
    private example.contract.AuthResponses.LoginResult login() { return client.login(new LoginProtocol.Credentials("alice", " exact ")); }

    @Test void sendsOnlyCredentialsToFixedPathWithoutIdentityOrAuthorizationHeaders() throws Exception {
        assertThat(login().user().id()).isEqualTo("alice");
        assertThat(target.get()).isEqualTo("POST /api/auth/login");
        assertThat(new ObjectMapper().readTree(requestBody.get())).isEqualTo(new ObjectMapper().readTree("{\"username\":\"alice\",\"password\":\" exact \"}"));
        assertThat(headers.get().getFirst("Authorization")).isNull();
        assertThat(headers.get().getFirst("Origin")).isNull();
        assertThat(headers.get().getFirst("X-User-Id")).isNull();
    }

    @Test void errorCodesAreFixedAndRedirectNeverFollowed() {
        body.set("sensitive upstream error");
        status.set(401); fails(401, "INVALID_CREDENTIALS");
        status.set(400); fails(400, "INVALID_ARGUMENT");
        for (int value : new int[] {403, 404, 500, 503}) { status.set(value); fails(503, "AUTH_UNAVAILABLE"); }
        status.set(307); requests.set(0); fails(503, "AUTH_UNAVAILABLE");
        assertThat(requests.get()).isEqualTo(1);
        assertThat(target.get()).isEqualTo("POST /api/auth/login");
    }

    @Test void successfulUpstreamStillRequiresStrictBoundedResponse() {
        for (String invalid : new String[] {"null", "{}", VALID + "{}", VALID.replace("900", "901"),
                VALID.replace("\"Bearer\"", "\"Bearer\",\"tokenType\":\"Bearer\""), VALID + " ".repeat(16385)}) {
            body.set(invalid); fails(503, "AUTH_UNAVAILABLE");
        }
        body.set(VALID); contentType.set("text/html"); fails(503, "AUTH_UNAVAILABLE");
        contentType.set("application/json"); body.set(VALID + " ".repeat(16384 - VALID.length())); assertThat(login().expiresIn()).isEqualTo(900);
    }

    @Test void rejectsDecoratedOrNonLoopbackOriginsWithoutNetwork() {
        for (String base : new String[] {"https://127.0.0.1:8081", "http://localhost:8081", "http://127.0.0.1:8081/path", "http://user@127.0.0.1:8081",
                "http://127.0.0.1:8081?query=1", "http://127.0.0.1:8081#fragment"})
            assertThatThrownBy(() -> new LoginRelayClient(URI.create(base))).isInstanceOf(IllegalArgumentException.class);
        assertThat(requests.get()).isZero();
    }

    private void fails(int expected, String code) {
        assertThatThrownBy(this::login).isInstanceOfSatisfying(LoginRelayClient.Failure.class, error -> assertThat(error.status).isEqualTo(expected)).hasMessage(code);
    }
}
