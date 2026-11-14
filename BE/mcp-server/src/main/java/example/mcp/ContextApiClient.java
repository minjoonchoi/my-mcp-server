package example.mcp;

import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.function.Consumer;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import example.contract.ContextAccessResponses.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.util.UriBuilder;

/** Fixed, read-only API surface for contextual access; never forwards the incoming bearer. */
@Component
public class ContextApiClient {
    private final RestClient client;
    private final ObjectMapper json;
    private final DelegatedTokenIssuer tokens;

    public ContextApiClient(ObjectMapper json, DelegatedTokenIssuer tokens, @Value("${access.base-url}") URI baseUrl) {
        if (!"http".equals(baseUrl.getScheme()) || !"127.0.0.1".equals(baseUrl.getHost())
                || baseUrl.getRawUserInfo() != null || baseUrl.getRawQuery() != null
                || baseUrl.getRawFragment() != null || !baseUrl.getPath().isEmpty()) {
            throw new IllegalArgumentException("Access base URL must be a loopback HTTP origin");
        }
        var http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(1))
                .followRedirects(HttpClient.Redirect.NEVER).build();
        var factory = new JdkClientHttpRequestFactory(http);
        factory.setReadTimeout(Duration.ofSeconds(2));
        this.client = RestClient.builder().baseUrl(baseUrl.toString()).requestFactory(factory).build();
        this.json = json.copy().enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_MISSING_CREATOR_PROPERTIES)
                .enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES)
                .enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
                .disable(com.fasterxml.jackson.databind.MapperFeature.ALLOW_COERCION_OF_SCALARS);
        this.tokens = tokens;
    }

    public PageList pages(Jwt caller, String after, int limit) {
        var result = get("/api/me/pages", caller, uri -> page(uri, after, limit), PageList.class);
        ContextResponseValidation.pages(result, after, limit);
        return result;
    }

    public EndpointList endpoints(Jwt caller, String pageId, String after, int limit) {
        var result = get("/api/me/pages/" + pageId + "/endpoints", caller,
                uri -> page(uri, after, limit), EndpointList.class);
        ContextResponseValidation.endpoints(result, pageId, after, limit);
        return result;
    }

    public FieldList fields(Jwt caller, String pageId, String endpointId, String direction, String after, int limit) {
        var result = get("/api/me/pages/" + pageId + "/endpoints/" + endpointId + "/fields", caller,
                uri -> { page(uri, after, limit); uri.queryParam("direction", direction); }, FieldList.class);
        ContextResponseValidation.fields(result, pageId, endpointId, direction, after, limit);
        return result;
    }

    public Decision check(Jwt caller, String targetType, String pageId, String endpointId, String fieldId) {
        var result = get("/api/me/context-access", caller, uri -> {
            uri.queryParam("targetType", targetType).queryParam("pageId", pageId);
            if (endpointId != null) uri.queryParam("endpointId", endpointId);
            if (fieldId != null) uri.queryParam("fieldId", fieldId);
        }, Decision.class);
        ContextResponseValidation.decision(result, targetType, pageId, endpointId, fieldId);
        return result;
    }

    private static void page(UriBuilder uri, String after, int limit) {
        uri.queryParam("limit", limit);
        if (after != null) uri.queryParam("after", after);
    }

    private <T> T get(String path, Jwt caller, Consumer<UriBuilder> query, Class<T> type) {
        try {
            return client.get().uri(uri -> { uri.path(path); query.accept(uri); return uri.build(); })
                    .headers(headers -> headers.setBearerAuth(tokens.issue(caller, false)))
                    .exchange((request, response) -> {
                        int status = response.getStatusCode().value();
                        if (status == 401 || status == 403) throw new AccessApiClient.ApiFailure("UPSTREAM_ACCESS_DENIED");
                        if (status == 404) throw new AccessApiClient.ApiFailure("NOT_FOUND");
                        if (status == 400) throw new AccessApiClient.ApiFailure("INVALID_ARGUMENT");
                        if (status != 200) throw new AccessApiClient.ApiFailure("UPSTREAM_UNAVAILABLE");
                        MediaType contentType = response.getHeaders().getContentType();
                        if (contentType == null || !MediaType.APPLICATION_JSON.isCompatibleWith(contentType)) throw invalidResponse();
                        byte[] body = response.getBody().readNBytes(16385);
                        if (body.length > 16384) throw invalidResponse();
                        try { return json.readValue(body, type); }
                        catch (JsonProcessingException malformed) { throw invalidResponse(); }
                    });
        } catch (AccessApiClient.ApiFailure safe) {
            throw safe;
        } catch (Exception unavailable) {
            throw new AccessApiClient.ApiFailure("UPSTREAM_UNAVAILABLE");
        }
    }

    private static AccessApiClient.ApiFailure invalidResponse() {
        return new AccessApiClient.ApiFailure("UPSTREAM_INVALID_RESPONSE");
    }
}
