package example.mcp;

import java.util.LinkedHashMap;
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

/** Additive tools; the existing generic resource contracts stay unchanged. */
@Configuration(proxyBeanMethods = false)
public class ContextToolRegistration {
    @Bean
    List<SyncToolSpecification> contextToolSpecifications(ContextTools securedTools, ObjectMapper json) {
        return List.of(
                tool("list_my_pages", "현재 사용자가 접근 가능한 어드민 페이지를 조회합니다. authz.read 필요. 최대 5개씩 반환하며 nextCursor를 after로 전달해 다음 목록을 조회합니다.",
                        paged(Map.of()), List.of(), securedTools::listMyPages, json),
                tool("list_my_page_endpoints", "현재 사용자가 지정한 페이지에서 호출할 수 있는 엔드포인트를 조회합니다. 페이지 권한만으로 엔드포인트 호출이 허용되지 않습니다. authz.read 필요.",
                        paged(Map.of("pageId", id())), List.of("pageId"), securedTools::listMyPageEndpoints, json),
                tool("list_my_endpoint_fields", "지정한 페이지와 엔드포인트에서 사용할 수 있는 요청(REQUEST) 또는 응답(RESPONSE) 필드를 조회합니다. 같은 엔드포인트라도 페이지별 허용 필드가 다릅니다. authz.read 필요.",
                        paged(Map.of("pageId", id(), "endpointId", id(), "direction", enumeration("REQUEST", "RESPONSE"))),
                        List.of("pageId", "endpointId", "direction"), securedTools::listMyEndpointFields, json),
                tool("check_my_context_access", "페이지·엔드포인트·필드 접근 여부와 내 역할/정책 근거, 최초 부족한 권한 단계를 확인합니다. PAGE는 pageId만, ENDPOINT는 endpointId 추가, FIELD는 endpointId와 fieldId 추가가 필수입니다. 비공개 대상의 존재나 미보유 정책은 공개하지 않습니다. authz.read 필요.",
                        Map.of("targetType", enumeration("PAGE", "ENDPOINT", "FIELD"), "pageId", id(), "endpointId", id(), "fieldId", id()),
                        List.of("targetType", "pageId"), securedTools::checkMyContextAccess, json));
    }

    private static Map<String, Object> id() {
        return Map.of("type", "string", "pattern", "^[A-Za-z0-9_-]{1,64}$", "minLength", 1, "maxLength", 64);
    }

    private static Map<String, Object> enumeration(String... values) {
        return Map.of("type", "string", "enum", List.of(values));
    }

    private static Map<String, Object> paged(Map<String, Object> required) {
        Map<String, Object> properties = new LinkedHashMap<>(required);
        properties.put("after", id());
        properties.put("limit", Map.of("type", "integer", "minimum", 1, "maximum", 5, "default", 5));
        return properties;
    }

    private static SyncToolSpecification tool(String name, String description, Map<String, Object> properties,
            List<String> required, Function<Map<String, Object>, Object> securedCall, ObjectMapper json) {
        var schema = new McpSchema.JsonSchema("object", properties, required, false, null, null);
        var definition = McpSchema.Tool.builder().name(name).description(description).inputSchema(schema)
                .annotations(new McpSchema.ToolAnnotations(name, true, false, true, false, false)).build();
        return new SyncToolSpecification(definition, (context, request) -> {
            try {
                // References target the injected security proxy; no direct self-invocation.
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
