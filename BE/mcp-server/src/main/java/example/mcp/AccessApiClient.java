package example.mcp;

import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import com.fasterxml.jackson.databind.ObjectMapper;
import example.contract.AccessResponses.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.HttpMethod;
import java.util.Map;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
public class AccessApiClient {
    private final RestClient client;
    private final ObjectMapper json;
    private final DelegatedTokenIssuer tokens;

    public AccessApiClient(ObjectMapper json, DelegatedTokenIssuer tokens, @Value("${access.base-url}") URI baseUrl) {
        if (!"http".equals(baseUrl.getScheme()) || !"127.0.0.1".equals(baseUrl.getHost())
                || baseUrl.getRawUserInfo() != null || baseUrl.getRawQuery() != null
                || baseUrl.getRawFragment() != null || !baseUrl.getPath().isEmpty()) {
            throw new IllegalArgumentException("Access base URL must be a loopback HTTP origin");
        }
        var httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(1))
                .followRedirects(HttpClient.Redirect.NEVER).build();
        var factory = new JdkClientHttpRequestFactory(httpClient);
        factory.setReadTimeout(Duration.ofSeconds(2));
        this.client = RestClient.builder().baseUrl(baseUrl.toString()).requestFactory(factory).build();
        this.json = json.copy().enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_MISSING_CREATOR_PROPERTIES)
                .enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES)
                .disable(com.fasterxml.jackson.databind.MapperFeature.ALLOW_COERCION_OF_SCALARS);
        this.tokens = tokens;
    }

    public MyAccess me(Jwt caller) {
        MyAccess result = get("/api/me", caller, MyAccess.class);
        AccessResponseValidation.myAccess(result);
        return result;
    }

    public ResourceAccess resource(Jwt caller, String resourceId) {
        ResourceAccess result = get("/api/me/resources/" + resourceId + "/access", caller, ResourceAccess.class);
        AccessResponseValidation.resourceAccess(result, resourceId);
        return result;
    }

    private <T> T get(String fixedPath, Jwt caller, Class<T> type) {
        return exchange(HttpMethod.GET, fixedPath, caller, type, false);
    }

    public CatalogList catalog(Jwt caller, String entity) {
        return AccessResponseValidation.catalog(get("/api/catalog/"+entity,caller,CatalogList.class),entity);
    }
    public CatalogDetail detail(Jwt caller, String entity, String id) {
        return AccessResponseValidation.detail(get("/api/catalog/"+entity+"/"+id,caller,CatalogDetail.class),entity,id);
    }
    public RequestableRoles requestable(Jwt caller) {
        var result=get("/api/me/requestable-roles",caller,RequestableRoles.class);
        AccessResponseValidation.requestable(result); return result;
    }
    public MutationResult change(Jwt caller,String operation,Map<String,String> targets) {
        String path = switch(operation) {
            case "assign_role_to_user", "remove_role_from_user" -> "/api/manage/users/"+targets.get("userId")+"/roles/"+targets.get("roleId");
            case "attach_policy_to_role", "detach_policy_from_role" -> "/api/manage/roles/"+targets.get("roleId")+"/policies/"+targets.get("policyId");
            case "attach_resource_to_policy", "detach_resource_from_policy" -> "/api/manage/policies/"+targets.get("policyId")+"/resources/"+targets.get("resourceId");
            default -> throw new IllegalArgumentException("INVALID_ARGUMENT");
        };
        boolean add=operation.startsWith("assign") || operation.startsWith("attach");
        var result=exchange(add?HttpMethod.PUT:HttpMethod.DELETE,path,caller,MutationResult.class,true);
        AccessResponseValidation.mutation(result,operation,targets,add); return result;
    }

    private <T> T exchange(HttpMethod method, String fixedPath, Jwt caller, Class<T> type, boolean management) {
        try {
            // A new signed credential for the verified caller; never a shared mutable Authorization header.
            return client.method(method).uri(fixedPath).headers(h -> h.setBearerAuth(tokens.issue(caller,management)))
                    .exchange((request, response) -> {
                        int status = response.getStatusCode().value();
                        if (status == 401) throw new ApiFailure("UPSTREAM_ACCESS_DENIED");
                        if (status == 403) throw new ApiFailure(management ? "FORBIDDEN" : "UPSTREAM_ACCESS_DENIED");
                        if (status == 404) throw new ApiFailure("NOT_FOUND");
                        if (status == 400) throw new ApiFailure("INVALID_ARGUMENT");
                        if (status != 200) throw new ApiFailure("UPSTREAM_UNAVAILABLE");
                        byte[] body = response.getBody().readNBytes(16385);
                        if (body.length > 16384) throw new ApiFailure("UPSTREAM_INVALID_RESPONSE");
                        return json.readValue(body, type);
                    });
        } catch (ApiFailure safe) {
            throw safe;
        } catch (Exception ignored) {
            throw new ApiFailure("UPSTREAM_UNAVAILABLE");
        }
    }

    public static final class ApiFailure extends RuntimeException {
        public ApiFailure(String code) { super(code, null, false, false); }
    }
}
