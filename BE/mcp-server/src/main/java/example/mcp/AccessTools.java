package example.mcp;

import java.util.Map;
import java.util.Set;
import example.contract.AccessResponses.*;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;

@Service
public class AccessTools {
    private final AccessApiClient api;
    public AccessTools(AccessApiClient api) { this.api = api; }

    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public MyAccess getMyAccess(Map<String, Object> arguments) {
        if (arguments == null || !arguments.isEmpty()) throw invalid();
        return api.me(caller());
    }

    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public ResourceAccess checkMyResourceAccess(Map<String, Object> arguments) {
        String id = oneString(arguments, "resourceId");
        if (!id.matches("R[0-9]{3}")) throw invalid();
        return api.resource(caller(), id);
    }

    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public Explanation explainAuthorization(Map<String, Object> arguments) {
        String concept = oneString(arguments, "concept");
        String explanation = switch (concept) {
            case "overview" -> "사용자에게 역할이 연결되고, 역할에 정책이 연결되며, 정책이 리소스 접근을 허용합니다. 실제 내 권한은 API 조회 결과로 확인합니다.";
            case "permission" -> "이 예제의 권한은 정책이 리소스 접근을 허용하는 관계입니다. MCP 접속 scope는 실제 리소스 접근 권한과 별개입니다.";
            case "role" -> "역할은 사용자의 업무에 맞춰 여러 정책을 묶습니다. 한 사용자는 여러 역할을 가질 수 있습니다.";
            case "policy" -> "정책은 접근 가능한 리소스를 정의합니다. 같은 정책이 여러 역할에서 부여될 수 있습니다.";
            case "resource" -> "리소스는 접근 대상입니다. 허용 여부와 역할·정책 경로는 API가 현재 사용자를 기준으로 계산합니다.";
            default -> throw invalid();
        };
        return new Explanation(concept, explanation);
    }

    private static String oneString(Map<String, Object> arguments, String key) {
        if (arguments == null || !arguments.keySet().equals(Set.of(key)) || !(arguments.get(key) instanceof String value)) throw invalid();
        return value;
    }
    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public CatalogList listAccessEntities(Map<String,Object> arguments) {
        String entity=oneString(arguments,"entity"); validEntity(entity); return api.catalog(caller(),entity);
    }
    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public CatalogDetail getAccessEntity(Map<String,Object> arguments) {
        Map<String,String> values=strings(arguments,"entity","id");
        String entity=values.get("entity"), id=values.get("id"); validEntity(entity); validId(id,entity.equals("resources"));
        return api.detail(caller(),entity,id);
    }
    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public RequestableRoles getRequestableRoles(Map<String,Object> arguments) {
        if(arguments==null || !arguments.isEmpty()) throw invalid(); return api.requestable(caller());
    }
    @PreAuthorize("hasAuthority('SCOPE_authz.manage')")
    public MutationResult assignRoleToUser(Map<String,Object> a) { return change("assign_role_to_user",a,"userId","roleId"); }
    @PreAuthorize("hasAuthority('SCOPE_authz.manage')")
    public MutationResult removeRoleFromUser(Map<String,Object> a) { return change("remove_role_from_user",a,"userId","roleId"); }
    @PreAuthorize("hasAuthority('SCOPE_authz.manage')")
    public MutationResult attachPolicyToRole(Map<String,Object> a) { return change("attach_policy_to_role",a,"roleId","policyId"); }
    @PreAuthorize("hasAuthority('SCOPE_authz.manage')")
    public MutationResult detachPolicyFromRole(Map<String,Object> a) { return change("detach_policy_from_role",a,"roleId","policyId"); }
    @PreAuthorize("hasAuthority('SCOPE_authz.manage')")
    public MutationResult attachResourceToPolicy(Map<String,Object> a) { return change("attach_resource_to_policy",a,"policyId","resourceId"); }
    @PreAuthorize("hasAuthority('SCOPE_authz.manage')")
    public MutationResult detachResourceFromPolicy(Map<String,Object> a) { return change("detach_resource_from_policy",a,"policyId","resourceId"); }
    private MutationResult change(String operation,Map<String,Object> args,String left,String right) {
        var targets=strings(args,left,right); targets.forEach((key,value)->validId(value,key.equals("resourceId")));
        return api.change(caller(),operation,targets);
    }
    private static Map<String,String> strings(Map<String,Object> args,String first,String second) {
        if(args==null || !args.keySet().equals(Set.of(first,second)) || !(args.get(first) instanceof String a) || !(args.get(second) instanceof String b)) throw invalid();
        return Map.of(first,a,second,b);
    }
    private static void validEntity(String entity) { if(!Set.of("users","roles","policies","resources").contains(entity)) throw invalid(); }
    private static void validId(String id,boolean resource) { if(!id.matches(resource?"R[0-9]{3}":"[A-Za-z0-9_-]{1,64}")) throw invalid(); }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("INVALID_ARGUMENT"); }
    private static Jwt caller() { return (Jwt) SecurityContextHolder.getContext().getAuthentication().getPrincipal(); }
}
