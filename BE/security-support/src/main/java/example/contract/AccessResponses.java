package example.contract;

import java.util.List;
import java.util.Map;

/** Wire DTOs shared by both apps; no secrets or arbitrary upstream properties. */
public final class AccessResponses {
    private AccessResponses() {}
    public record User(String id, String name, String description, List<String> roleIds) {}
    public record Role(String id, String name, String description, List<String> policyIds) {}
    public record Policy(String id, String name, String description, List<String> resourceIds,
                         List<String> grantedByRoleIds) {}
    public record Grant(String roleId, String policyId) {}
    public record Resource(String id, String name, String description) {}
    public record GrantedResource(String id, String name, String description, List<Grant> grants) {}
    public record MyAccess(User user, List<Role> roles, List<Policy> policies, List<GrantedResource> resources) {}
    public record ResourceAccess(String resourceId, boolean allowed, Resource resource, List<Grant> grants) {}
    public record Explanation(String concept, String explanation) {}
    public record EntitySummary(String id, String name, String description) {}
    public record CatalogList(String entity, List<Map<String, Object>> items) {}
    public record CatalogDetail(String entity, Map<String, Object> item) {}
    public record RequestableRoles(List<EntitySummary> roles) {}
    public record Impact(List<String> affectedUserIds) {}
    public record MutationResult(String operation, boolean changed, Map<String, String> relationship,
                                 boolean present, Impact impact) {}
}
