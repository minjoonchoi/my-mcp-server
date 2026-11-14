package example.mcp;

import java.util.*;
import example.contract.AccessResponses.*;

/** Reject inconsistent upstream responses; DTO decoding discards unknown fields. */
final class AccessResponseValidation {
    private AccessResponseValidation() {}
    private static void require(boolean condition) {
        if (!condition) throw new AccessApiClient.ApiFailure("UPSTREAM_INVALID_RESPONSE");
    }
    private static void entity(String id, String name, String description) {
        require(id != null && !id.isBlank() && id.length() <= 100
                && name != null && !name.isBlank() && name.length() <= 200
                && description != null && description.length() <= 500);
    }
    private static <T> Set<T> unique(List<T> values) {
        require(values != null && values.size() <= 100 && values.stream().noneMatch(Objects::isNull));
        var set = new HashSet<>(values);
        require(set.size() == values.size());
        return set;
    }
    static void myAccess(MyAccess a) {
        // The API maps verified (original issuer, subject) to its independent local user ID.
        // This boundary validates the returned graph, not an invented id == subject rule.
        require(a != null && a.user() != null);
        entity(a.user().id(), a.user().name(), a.user().description());
        unique(a.roles()); unique(a.policies()); unique(a.resources());
        Map<String, Role> roles = new TreeMap<>();
        Map<String, Policy> policies = new TreeMap<>();
        Map<String, GrantedResource> resources = new TreeMap<>();
        for (Role r : a.roles()) {
            entity(r.id(), r.name(), r.description()); unique(r.policyIds());
            require(roles.put(r.id(), r) == null);
        }
        require(unique(a.user().roleIds()).equals(roles.keySet()));
        for (Policy p : a.policies()) {
            entity(p.id(), p.name(), p.description()); unique(p.resourceIds()); unique(p.grantedByRoleIds());
            require(policies.put(p.id(), p) == null);
            var expectedRoles = roles.values().stream().filter(r -> r.policyIds().contains(p.id())).map(Role::id).collect(java.util.stream.Collectors.toSet());
            require(!expectedRoles.isEmpty() && expectedRoles.equals(new HashSet<>(p.grantedByRoleIds())));
        }
        require(roles.values().stream().flatMap(r -> r.policyIds().stream()).collect(java.util.stream.Collectors.toSet()).equals(policies.keySet()));
        for (GrantedResource r : a.resources()) {
            entity(r.id(), r.name(), r.description());
            require(r.id().matches("R[0-9]{3}") && resources.put(r.id(), r) == null);
            var expected = new HashSet<Grant>();
            for (Policy p : policies.values()) if (p.resourceIds().contains(r.id()))
                for (String role : p.grantedByRoleIds()) expected.add(new Grant(role, p.id()));
            require(!expected.isEmpty() && unique(r.grants()).equals(expected));
        }
        require(policies.values().stream().flatMap(p -> p.resourceIds().stream()).collect(java.util.stream.Collectors.toSet()).equals(resources.keySet()));
    }
    static void resourceAccess(ResourceAccess a, String resourceId) {
        require(a != null && resourceId.equals(a.resourceId()));
        Set<Grant> grants = unique(a.grants());
        if (!a.allowed()) { require(a.resource() == null && grants.isEmpty()); return; }
        require(a.resource() != null && resourceId.equals(a.resource().id()) && !grants.isEmpty());
        entity(a.resource().id(), a.resource().name(), a.resource().description());
        for (Grant g : grants) require(g.roleId() != null && !g.roleId().isBlank()
                && g.policyId() != null && !g.policyId().isBlank());
    }
    private static Map<String,Object> catalogItem(String entity,Map<String,Object> item) {
        require(item!=null && item.get("id") instanceof String && item.get("name") instanceof String && item.get("description") instanceof String);
        String id=(String)item.get("id"); entity(id,(String)item.get("name"),(String)item.get("description"));
        require(id.matches(entity.equals("resources")?"R[0-9]{3}":"[A-Za-z0-9_-]{1,64}"));
        Map<String,Object> safe=new LinkedHashMap<>();
        for(String key:List.of("id","name","description")) safe.put(key,item.get(key));
        String links=switch(entity){case "users"->"roleIds";case "roles"->"policyIds";case "policies"->"resourceIds";default->null;};
        if(links!=null) {
            require(item.get(links) instanceof List<?>);
            List<?> ids=(List<?>)item.get(links); unique(ids);
            for(Object value:ids) require(value instanceof String text && text.matches(links.equals("resourceIds")?"R[0-9]{3}":"[A-Za-z0-9_-]{1,64}"));
            safe.put(links,ids);
        }
        if(!entity.equals("users")) { require(item.get("protected") instanceof Boolean); safe.put("protected",item.get("protected")); }
        if(entity.equals("roles")) { require(item.get("requestable") instanceof Boolean); safe.put("requestable",item.get("requestable")); }
        return safe;
    }
    static CatalogList catalog(CatalogList result,String entity) {
        require(result!=null && entity.equals(result.entity())); unique(result.items());
        var safe=result.items().stream().map(item->catalogItem(entity,item)).toList();
        require(safe.stream().map(item->item.get("id")).distinct().count()==safe.size());
        return new CatalogList(entity,safe);
    }
    static CatalogDetail detail(CatalogDetail result,String entity,String id) {
        require(result!=null && entity.equals(result.entity())); var safe=catalogItem(entity,result.item());
        require(id.equals(safe.get("id"))); return new CatalogDetail(entity,safe);
    }
    static void requestable(RequestableRoles result) {
        require(result!=null); unique(result.roles());
        for(var role:result.roles()) { entity(role.id(),role.name(),role.description()); require(role.id().matches("[A-Za-z0-9_-]{1,64}")); }
        require(result.roles().stream().map(EntitySummary::id).distinct().count()==result.roles().size());
    }
    static void mutation(MutationResult result,String operation,Map<String,String> targets,boolean present) {
        require(result!=null && operation.equals(result.operation()) && targets.equals(result.relationship())
                && present==result.present() && result.impact()!=null);
        unique(result.impact().affectedUserIds());
        for(String id:result.impact().affectedUserIds()) require(id.matches("[A-Za-z0-9_-]{1,64}"));
    }

}
