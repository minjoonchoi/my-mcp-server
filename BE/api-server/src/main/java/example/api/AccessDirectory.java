package example.api;

import java.util.*;
import example.contract.AccessResponses.*;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.context.annotation.Profile;

/** API-owned allow-only data. Subject and original issuer were authenticated by the API. */
@Service
@Profile("fixture")
public class AccessDirectory implements AccessRepository {
    private record Identity(String issuer, String subject) {}
    private record PolicyDefinition(String id, String name, String description, List<String> resourceIds) {}
    private static final String ORIGINAL_ISSUER = "https://demo-issuer.invalid";
    private final Map<Identity, User> users = new HashMap<>(Map.of(
            new Identity(ORIGINAL_ISSUER, "alice"), new User("alice", "앨리스", "분석 및 편집 담당자", List.of("role-analyst", "role-editor")),
            new Identity(ORIGINAL_ISSUER, "bob"), new User("bob", "밥", "청구 조회 담당자", List.of("role-billing")),
            new Identity(ORIGINAL_ISSUER, "carol"), new User("carol", "캐럴", "역할이 아직 없는 사용자", List.of())));
    private final Map<String, Role> roles = new TreeMap<>(Map.of(
            "role-analyst", new Role("role-analyst", "분석가", "보고서와 분석 정보 조회", List.of("policy-reports", "policy-shared")),
            "role-editor", new Role("role-editor", "편집자", "편집 자료 접근", List.of("policy-edit", "policy-shared")),
            "role-billing", new Role("role-billing", "청구 담당", "청구 정보와 공용 보고서 조회", List.of("policy-billing", "policy-shared"))));
    private final Map<String, PolicyDefinition> policies = new TreeMap<>(Map.of(
            "policy-reports", new PolicyDefinition("policy-reports", "분석 조회", "보고서와 대시보드 접근 허용", List.of("R100", "R200")),
            "policy-shared", new PolicyDefinition("policy-shared", "공용 보고서", "공용 보고서 접근 허용", List.of("R100")),
            "policy-edit", new PolicyDefinition("policy-edit", "편집 자료", "대시보드와 문서 접근 허용", List.of("R200", "R300")),
            "policy-billing", new PolicyDefinition("policy-billing", "청구 조회", "청구 내역 접근 허용", List.of("R400"))));
    private final Map<String, Resource> resources = new TreeMap<>(Map.of(
            "R100", new Resource("R100", "공용 보고서", "조직의 분석 보고서"),
            "R200", new Resource("R200", "분석 대시보드", "분석 결과 화면"),
            "R300", new Resource("R300", "편집 문서", "편집 담당 자료"),
            "R400", new Resource("R400", "청구 내역", "청구 담당 자료")));
    private static final Comparator<Grant> GRANT_ORDER = Comparator.comparing(Grant::roleId).thenComparing(Grant::policyId);

    public synchronized MyAccess access(String originalIssuer, String subject) {
        User user = users.get(new Identity(originalIssuer, subject));
        if (user == null) throw new AccessDeniedException("ACCESS_DENIED");
        var ownRoles = user.roleIds().stream().distinct().sorted().map(roles::get).toList();
        var policyRoles = new TreeMap<String, SortedSet<String>>();
        var resourceGrants = new TreeMap<String, SortedSet<Grant>>();
        for (Role role : ownRoles) {
            for (String policyId : role.policyIds()) {
                policyRoles.computeIfAbsent(policyId, key -> new TreeSet<>()).add(role.id());
                for (String resourceId : policies.get(policyId).resourceIds()) {
                    resourceGrants.computeIfAbsent(resourceId, key -> new TreeSet<>(GRANT_ORDER))
                            .add(new Grant(role.id(), policyId));
                }
            }
        }
        var ownPolicies = policyRoles.entrySet().stream().map(entry -> {
            var p = policies.get(entry.getKey());
            return new Policy(p.id(), p.name(), p.description(), p.resourceIds(), List.copyOf(entry.getValue()));
        }).toList();
        var ownResources = resourceGrants.entrySet().stream().map(entry -> {
            var r = resources.get(entry.getKey());
            return new GrantedResource(r.id(), r.name(), r.description(), List.copyOf(entry.getValue()));
        }).toList();
        return new MyAccess(user, ownRoles, ownPolicies, ownResources);
    }

    public ResourceAccess resourceAccess(String issuer, String subject, String resourceId) {
        return access(issuer, subject).resources().stream().filter(r -> resourceId.equals(r.id()))
                .findFirst().map(r -> new ResourceAccess(resourceId, true,
                        new Resource(r.id(), r.name(), r.description()), r.grants()))
                .orElseGet(() -> new ResourceAccess(resourceId, false, null, List.of()));
    }
    public AccessDirectory() {
        users.put(new Identity(ORIGINAL_ISSUER,"local-id-demo"), new User("U900","독립 ID 사용자","subject와 로컬 ID가 다른 무역할 사용자",List.of()));
        users.put(new Identity(ORIGINAL_ISSUER,"admin"), new User("admin","로컬 관리자","개발용 보호 시스템 역할 보유자",List.of("SYSTEM_ADMIN")));
        roles.put("SYSTEM_ADMIN",new Role("SYSTEM_ADMIN","시스템 관리자","DB 관리 권한의 보호된 시작점",List.of("SYSTEM_POLICY")));
        policies.put("SYSTEM_POLICY",new PolicyDefinition("SYSTEM_POLICY","시스템 정책","보호된 시스템 정책",List.of("R900")));
        resources.put("R900",new Resource("R900","시스템 리소스","보호된 시스템 리소스"));
        for (String policyId : ContextFixtures.policyIds())
            policies.put(policyId, new PolicyDefinition(policyId, policyId, "Page-scoped authorization rule", List.of()));
        ContextFixtures.ROLE_POLICIES.forEach((roleId, policyIds) -> {
            Role role = roles.get(roleId);
            var combined = new TreeSet<>(role.policyIds());
            combined.addAll(policyIds);
            roles.put(roleId, new Role(role.id(), role.name(), role.description(), List.copyOf(combined)));
        });
    }

    /** Snapshot effective assignments under the same monitor used by relationship changes. */
    synchronized ContextSnapshot contextSnapshot(String issuer, String subject) {
        User user = users.get(new Identity(issuer, subject));
        if (user == null) throw new ApiProblem(403, "FORBIDDEN");
        var assignments = user.roleIds().stream().flatMap(roleId -> roles.get(roleId).policyIds().stream()
                .map(policyId -> new example.contract.ContextAccessResponses.Grant(roleId, policyId))).toList();
        return ContextFixtures.snapshot(assignments);
    }
    record LoginIdentity(String id, String name, boolean administrator) {}
    synchronized LoginIdentity loginIdentity(String issuer, String subject) {
        User user = users.get(new Identity(issuer, subject));
        return user == null ? null : new LoginIdentity(user.id(), user.name(), admin(user));
    }
    private boolean admin(User user) { return user.roleIds().contains("SYSTEM_ADMIN"); }
    private boolean protectedId(String entity,String id) {
        return (entity.equals("roles") && id.equals("SYSTEM_ADMIN")) || (entity.equals("policies") && id.equals("SYSTEM_POLICY")) || (entity.equals("resources") && id.equals("R900"));
    }
    private boolean exists(String entity,String id) {
        return switch(entity) {
            case "users" -> users.values().stream().anyMatch(user -> user.id().equals(id));
            case "roles" -> roles.containsKey(id);
            case "policies" -> policies.containsKey(id);
            default -> resources.containsKey(id);
        };
    }
    @Override
    public synchronized CatalogList catalog(String issuer,String subject,String entity) {
        ManagementContract.validEntity(entity);
        var own = access(issuer,subject);
        boolean full = admin(own.user());
        var items = new ArrayList<Map<String,Object>>();
        switch (entity) {
            case "users" -> users.values().stream().filter(u -> full || u.id().equals(own.user().id())).sorted(Comparator.comparing(User::id))
                    .forEach(u -> items.add(Map.of("id",u.id(),"name",u.name(),"description",u.description(),"roleIds",u.roleIds())));
            case "roles" -> roles.values().stream().filter(r -> full || own.user().roleIds().contains(r.id())).forEach(r ->
                    items.add(Map.of("id",r.id(),"name",r.name(),"description",r.description(),"policyIds",r.policyIds(),"protected",protectedId(entity,r.id()),"requestable",!protectedId(entity,r.id()))));
            case "policies" -> policies.values().stream().filter(p -> full || own.policies().stream().anyMatch(o -> o.id().equals(p.id()))).forEach(p ->
                    items.add(Map.of("id",p.id(),"name",p.name(),"description",p.description(),"resourceIds",p.resourceIds(),"protected",protectedId(entity,p.id()))));
            default -> resources.values().stream().filter(r -> full || own.resources().stream().anyMatch(o -> o.id().equals(r.id()))).forEach(r ->
                    items.add(Map.of("id",r.id(),"name",r.name(),"description",r.description(),"protected",protectedId(entity,r.id()))));
        }
        return new CatalogList(entity,List.copyOf(items));
    }
    @Override
    public synchronized RequestableRoles requestable(String issuer,String subject) {
        var own = access(issuer,subject).user();
        return new RequestableRoles(roles.values().stream().filter(r -> !protectedId("roles",r.id()) && !own.roleIds().contains(r.id()))
                .map(r -> new EntitySummary(r.id(),r.name(),r.description())).toList());
    }
    @Override
    public synchronized MutationResult change(String issuer,String subject,String operation,Map<String,String> targets) {
        if (!admin(access(issuer,subject).user())) throw new ApiProblem(403,"FORBIDDEN");
        var relation=ManagementContract.relation(operation);
        ManagementContract.validTargets(relation,targets);
        String left=targets.get(relation.leftKey()),right=targets.get(relation.rightKey());
        if (protectedId(relation.leftTable(),left) || protectedId(relation.rightTable(),right)) throw new ApiProblem(403,"FORBIDDEN");
        if (!exists(relation.leftTable(),left) || !exists(relation.rightTable(),right)) throw new ApiProblem(404,"NOT_FOUND");
        if (relation.table().equals("policy_resources") && ContextFixtures.contextualPolicy(left))
            throw new ApiProblem(400, "INVALID_ARGUMENT");
        List<String> affected;
        boolean changed;
        if (relation.table().equals("user_roles")) {
            var entry=users.entrySet().stream().filter(e -> e.getValue().id().equals(left)).findFirst().orElseThrow();
            User u=entry.getValue(); var links=new TreeSet<>(u.roleIds()); changed=relation.add()?links.add(right):links.remove(right);
            entry.setValue(new User(u.id(),u.name(),u.description(),List.copyOf(links))); affected=List.of(left);
        } else if (relation.table().equals("role_policies")) {
            affected=users.values().stream().filter(u -> u.roleIds().contains(left)).map(User::id).sorted().toList();
            Role r=roles.get(left); var links=new TreeSet<>(r.policyIds()); changed=relation.add()?links.add(right):links.remove(right);
            roles.put(left,new Role(r.id(),r.name(),r.description(),List.copyOf(links)));
        } else {
            affected=users.values().stream().filter(u -> u.roleIds().stream().anyMatch(id -> roles.get(id).policyIds().contains(left))).map(User::id).sorted().toList();
            var p=policies.get(left); var links=new TreeSet<>(p.resourceIds()); changed=relation.add()?links.add(right):links.remove(right);
            policies.put(left,new PolicyDefinition(p.id(),p.name(),p.description(),List.copyOf(links)));
        }
        // Fixture profile models relationship state only; durable audit is verified against JDBC/MySQL.
        return new MutationResult(operation,changed,Map.copyOf(targets),relation.add(),new Impact(affected));
    }

}
