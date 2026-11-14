package example.mcp;

import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.oauth2.jwt.Jwt;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

/** Executed by the runtime owner as part of the normal JUnit build. */
class ContextApiClientTest {
    private HttpServer server;
    private ContextApiClient client;
    private DelegatedTokenIssuer tokens;
    private final Jwt caller = Jwt.withTokenValue("original-unit-bearer").header("alg", "RS256").subject("caller").build();
    private final AtomicReference<String> body = new AtomicReference<>("{\"items\":[],\"nextCursor\":null}");
    private final AtomicReference<String> contentType = new AtomicReference<>("application/json");
    private final AtomicReference<String> authorization = new AtomicReference<>();
    private final AtomicReference<String> target = new AtomicReference<>();
    private final AtomicInteger requests = new AtomicInteger();
    private final AtomicInteger status = new AtomicInteger(200);

    @BeforeEach void startApiBoundary() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/me", exchange -> {
            requests.incrementAndGet();
            authorization.set(exchange.getRequestHeaders().getFirst("Authorization"));
            target.set(exchange.getRequestMethod() + " " + exchange.getRequestURI());
            byte[] bytes = body.get().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", contentType.get());
            if (status.get() == 307) exchange.getResponseHeaders().set("Location", "/api/me/redirected");
            exchange.sendResponseHeaders(status.get(), bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        tokens = mock(DelegatedTokenIssuer.class);
        when(tokens.issue(caller, false)).thenReturn("delegated-unit-bearer");
        client = new ContextApiClient(new ObjectMapper(), tokens, URI.create("http://127.0.0.1:" + server.getAddress().getPort()));
    }

    @AfterEach void stopApiBoundary() { if (server != null) server.stop(0); }

    @Test void usesOnlyFixedGetPathAndFreshDelegationWithBoundedQuery() {
        assertThat(client.pages(caller, "page-before", 2).items()).isEmpty();
        assertThat(target.get()).isEqualTo("GET /api/me/pages?limit=2&after=page-before");
        assertThat(authorization.get()).isEqualTo("Bearer delegated-unit-bearer").doesNotContain(caller.getTokenValue());
        verify(tokens).issue(caller, false);
    }

    @Test void preservesPageContextAndDistinguishesNormalDenialFromTransportError() {
        body.set("{\"targetType\":\"FIELD\",\"pageId\":\"page-a\",\"endpointId\":\"endpoint-a\",\"fieldId\":\"field-a\",\"allowed\":false,\"reason\":\"PAGE_NOT_GRANTED\",\"stages\":[{\"stage\":\"PAGE\",\"allowed\":false,\"grants\":[],\"grantsTruncated\":false}]}");
        var decision = client.check(caller, "FIELD", "page-a", "endpoint-a", "field-a");
        assertThat(decision.allowed()).isFalse();
        assertThat(decision.reason()).isEqualTo("PAGE_NOT_GRANTED");
        assertThat(target.get()).isEqualTo("GET /api/me/context-access?targetType=FIELD&pageId=page-a&endpointId=endpoint-a&fieldId=field-a");
        body.set(body.get().replace("page-a", "page-other"));
        fails(() -> client.check(caller, "FIELD", "page-a", "endpoint-a", "field-a"), "UPSTREAM_INVALID_RESPONSE");
    }

    @Test void rejectsMalformedDuplicateMissingAndWrongContentResponses() {
        for (String invalid : new String[] {"not-json", "null", "{\"items\":[]}",
                "{\"items\":[],\"items\":[],\"nextCursor\":null}", "{\"items\":[],\"nextCursor\":null} {}"}) {
            body.set(invalid);
            fails(() -> client.pages(caller, null, 5), "UPSTREAM_INVALID_RESPONSE");
        }
        body.set("{\"items\":[],\"nextCursor\":null}");
        contentType.set("text/plain");
        fails(() -> client.pages(caller, null, 5), "UPSTREAM_INVALID_RESPONSE");
    }

    @Test void acceptsExactByteBudgetAndRejectsOneByteOverIt() {
        String valid = "{\"items\":[],\"nextCursor\":null}";
        body.set(valid + " ".repeat(16384 - valid.length()));
        assertThat(client.pages(caller, null, 5).items()).isEmpty();
        body.set(body.get() + " ");
        fails(() -> client.pages(caller, null, 5), "UPSTREAM_INVALID_RESPONSE");
    }

    @Test void sanitizesStatusesAndNeverFollowsRedirects() {
        body.set("private upstream details");
        status.set(403); fails(() -> client.pages(caller, null, 5), "UPSTREAM_ACCESS_DENIED");
        status.set(404); fails(() -> client.pages(caller, null, 5), "NOT_FOUND");
        status.set(400); fails(() -> client.pages(caller, null, 5), "INVALID_ARGUMENT");
        requests.set(0);
        status.set(307); fails(() -> client.pages(caller, null, 5), "UPSTREAM_UNAVAILABLE");
        assertThat(requests.get()).isEqualTo(1);
        assertThat(target.get()).isEqualTo("GET /api/me/pages?limit=5");
    }

    @Test void rejectsNonLoopbackOrDecoratedBaseOriginsBeforeAnyRequest() {
        for (String base : new String[] {"https://127.0.0.1:8081", "http://example.invalid", "http://user@127.0.0.1:8081",
                "http://127.0.0.1:8081/path", "http://127.0.0.1:8081?query=1", "http://127.0.0.1:8081#fragment"}) {
            assertThatThrownBy(() -> new ContextApiClient(new ObjectMapper(), tokens, URI.create(base))).isInstanceOf(IllegalArgumentException.class);
        }
        assertThat(requests.get()).isZero();
    }

    private static void fails(Runnable action, String code) {
        assertThatThrownBy(action::run).isInstanceOf(AccessApiClient.ApiFailure.class).hasMessage(code);
    }
}
