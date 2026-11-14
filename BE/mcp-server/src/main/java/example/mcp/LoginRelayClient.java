package example.mcp;

import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Map;
import example.contract.AuthResponses.LoginResult;
import example.security.LoginProtocol;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** A single fixed anonymous relay. No inbound headers or caller-controlled URL reach the API. */
@Component
public class LoginRelayClient {
    public static final class Failure extends RuntimeException {
        public final int status;
        public Failure(int status, String code) { super(code, null, false, false); this.status = status; }
    }
    private final RestClient client;

    public LoginRelayClient(@Value("${access.base-url}") URI baseUrl) {
        if (!"http".equals(baseUrl.getScheme()) || !"127.0.0.1".equals(baseUrl.getHost())
                || baseUrl.getRawUserInfo() != null || baseUrl.getRawQuery() != null
                || baseUrl.getRawFragment() != null || !baseUrl.getPath().isEmpty())
            throw new IllegalArgumentException("Access base URL must be a loopback HTTP origin");
        var http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(1))
                .followRedirects(HttpClient.Redirect.NEVER).build();
        var factory = new JdkClientHttpRequestFactory(http);
        factory.setReadTimeout(Duration.ofSeconds(2));
        client = RestClient.builder().baseUrl(baseUrl.toString()).requestFactory(factory).build();
    }

    public LoginResult login(LoginProtocol.Credentials credentials) {
        try {
            return client.post().uri("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("username", credentials.username(), "password", credentials.password()))
                    .exchange((request, response) -> {
                        int status = response.getStatusCode().value();
                        if (status == 400) throw new Failure(400, "INVALID_ARGUMENT");
                        if (status == 401) throw new Failure(401, "INVALID_CREDENTIALS");
                        if (status != 200) throw unavailable();
                        MediaType type = response.getHeaders().getContentType();
                        if (type == null || !MediaType.APPLICATION_JSON.isCompatibleWith(type)) throw unavailable();
                        return LoginProtocol.result(response.getBody().readNBytes(LoginProtocol.MAX_RESPONSE_BYTES + 1));
                    });
        } catch (Failure safe) { throw safe; }
        catch (Exception invalidOrUnavailable) { throw unavailable(); }
    }

    private static Failure unavailable() { return new Failure(503, "AUTH_UNAVAILABLE"); }
}
