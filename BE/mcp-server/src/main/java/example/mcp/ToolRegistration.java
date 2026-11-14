package example.mcp;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.modelcontextprotocol.server.McpStatelessServerFeatures.SyncToolSpecification;
import io.modelcontextprotocol.spec.McpSchema;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.access.AccessDeniedException;

@Configuration(proxyBeanMethods = false)
public class ToolRegistration {
    @Bean(name = "mcpServerObjectMapper", defaultCandidate = false)
    ObjectMapper mcpServerObjectMapper() {
        // Keep protocol optionals omitted, but preserve explicit null values inside structuredContent maps.
        var mapper = com.fasterxml.jackson.databind.json.JsonMapper.builder()
                .disable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .disable(com.fasterxml.jackson.databind.SerializationFeature.FAIL_ON_EMPTY_BEANS)
                .findAndAddModules().build();
        mapper.setDefaultPropertyInclusion(com.fasterxml.jackson.annotation.JsonInclude.Value.construct(
                com.fasterxml.jackson.annotation.JsonInclude.Include.NON_ABSENT,
                com.fasterxml.jackson.annotation.JsonInclude.Include.ALWAYS));
        return mapper;
    }

    @Bean
    List<SyncToolSpecification> accessToolSpecifications(AccessTools securedTools, ObjectMapper json) {
        return List.of(
                tool("get_my_access", "내 프로필, 역할, 정책, 접근 가능한 리소스와 부여 경로를 조회합니다. authz.read 필요.",
                        Map.of(), List.of(), securedTools::getMyAccess, json),
                tool("check_my_resource_access", "현재 사용자의 리소스 접근 여부와 역할·정책 부여 경로를 확인합니다. authz.read 필요.",
                        Map.of("resourceId", Map.of("type", "string", "pattern", "^R[0-9]{3}$", "minLength", 4,
                                "maxLength", 4, "description", "리소스 ID. 예: R100")),
                        List.of("resourceId"), securedTools::checkMyResourceAccess, json),
                tool("explain_authorization", "권한 개념을 설명합니다. 설명은 사용자의 실제 접근 권한이 아닙니다. authz.read 필요.",
                        Map.of("concept", Map.of("type", "string", "enum", List.of("overview", "permission", "role", "policy", "resource"))),
                        List.of("concept"), securedTools::explainAuthorization, json),
                tool("list_access_entities", "authz.read 필요. 일반 사용자는 본인 그래프, DB 관리자는 전체 카탈로그만 조회합니다.",
                        Map.of("entity",Map.of("type","string","enum",List.of("users","roles","policies","resources"))),List.of("entity"),securedTools::listAccessEntities,json),
                tool("get_access_entity", "authz.read 필요. 보이는 카탈로그 항목만 조회하며 비공개와 미존재 항목은 동일 NOT_FOUND입니다.",
                        Map.of("entity",Map.of("type","string","enum",List.of("users","roles","policies","resources")),"id",idSchema(false)),List.of("entity","id"),securedTools::getAccessEntity,json),
                tool("get_requestable_roles", "authz.read 필요. 아직 직접 보유하지 않은 신청 가능 비보호 역할의 공개 설명만 조회합니다.",
                        Map.of(),List.of(),securedTools::getRequestableRoles,json),
                mutation("assign_role_to_user","userId","roleId",securedTools::assignRoleToUser,json),
                mutation("remove_role_from_user","userId","roleId",securedTools::removeRoleFromUser,json),
                mutation("attach_policy_to_role","roleId","policyId",securedTools::attachPolicyToRole,json),
                mutation("detach_policy_from_role","roleId","policyId",securedTools::detachPolicyFromRole,json),
                mutation("attach_resource_to_policy","policyId","resourceId",securedTools::attachResourceToPolicy,json),
                mutation("detach_resource_from_policy","policyId","resourceId",securedTools::detachResourceFromPolicy,json));
    }

    private SyncToolSpecification tool(String name, String description, Map<String, Object> properties,
            List<String> required, Function<Map<String, Object>, Object> securedCall, ObjectMapper json) {
        return tool(name,description,properties,required,securedCall,json,false);
    }
    private Map<String,Object> idSchema(boolean resource) {
        return Map.of("type","string","pattern",resource?"^R[0-9]{3}$":"^[A-Za-z0-9_-]{1,64}$");
    }
    private SyncToolSpecification mutation(String name,String left,String right,Function<Map<String,Object>,Object> call,ObjectMapper json) {
        return tool(name,"authz.manage와 API의 현재 SYSTEM_ADMIN 역할 필요. 대상 ID는 인증 주체가 아닙니다. 보호 엔티티 변경 금지. 반복 요청은 멱등하며 changed/present는 DB 트랜잭션 직후 상태입니다.",
                Map.of(left,idSchema(false),right,idSchema(right.equals("resourceId"))),List.of(left,right),call,json,true);
    }
    private SyncToolSpecification tool(String name,String description,Map<String,Object> properties,List<String> required,
            Function<Map<String,Object>,Object> securedCall,ObjectMapper json,boolean management) {
        var schema = new McpSchema.JsonSchema("object", properties, required, false, null, null);
        var tool = McpSchema.Tool.builder().name(name).description(description).inputSchema(schema)
                .annotations(new McpSchema.ToolAnnotations(name, !management, management, true, false, false)).build();
        return new SyncToolSpecification(tool, (context, request) -> {
            try {
                // Method references target the injected Spring proxy, not self-invocation.
                Object result = securedCall.apply(request.arguments());
                Map<String, Object> structured = json.convertValue(result, new TypeReference<>() {});
                return McpSchema.CallToolResult.builder().structuredContent(structured)
                        .addTextContent(json.writeValueAsString(result)).isError(false).build();
            } catch (AccessDeniedException denied) {
                return error("FORBIDDEN");
            } catch (IllegalArgumentException invalid) {
                return error("INVALID_ARGUMENT");
            } catch (AccessApiClient.ApiFailure failure) {
                return error(failure.getMessage());
            } catch (Exception unexpected) {
                return error("INTERNAL_ERROR");
            }
        });
    }

    private static McpSchema.CallToolResult error(String code) {
        return McpSchema.CallToolResult.builder().isError(true).addTextContent(code).build();
    }
}
