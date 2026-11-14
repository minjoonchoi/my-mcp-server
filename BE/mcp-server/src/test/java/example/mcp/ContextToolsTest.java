package example.mcp;

import java.math.BigInteger;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import example.contract.ContextAccessResponses.*;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class ContextToolsTest {
    private final ContextApiClient api = mock(ContextApiClient.class);
    private final ContextTools tools = new ContextTools(api);
    private final Jwt caller = Jwt.withTokenValue("unit-caller").header("alg", "RS256").subject("verified-subject").build();

    @BeforeEach void authenticate() { SecurityContextHolder.getContext().setAuthentication(new JwtAuthenticationToken(caller)); }
    @AfterEach void clear() { SecurityContextHolder.clearContext(); }

    @Test void passesOnlyValidatedContextAndAuthenticatedCallerToFixedOperations() {
        tools.listMyPages(Map.of());
        tools.listMyPageEndpoints(Map.of("pageId", "page-customers", "after", "endpoint-previous", "limit", 2L));
        tools.listMyEndpointFields(Map.of("pageId", "page-customers", "endpointId", "endpoint-customer-get", "direction", "RESPONSE", "limit", BigInteger.ONE));
        tools.checkMyContextAccess(Map.of("targetType", "PAGE", "pageId", "page-customers"));
        tools.checkMyContextAccess(Map.of("targetType", "ENDPOINT", "pageId", "page-customers", "endpointId", "endpoint-customer-get"));
        tools.checkMyContextAccess(Map.of("targetType", "FIELD", "pageId", "page-customers", "endpointId", "endpoint-customer-get", "fieldId", "field-name"));
        verify(api).pages(caller, null, 5);
        verify(api).endpoints(caller, "page-customers", "endpoint-previous", 2);
        verify(api).fields(caller, "page-customers", "endpoint-customer-get", "RESPONSE", null, 1);
        verify(api).check(caller, "PAGE", "page-customers", null, null);
        verify(api).check(caller, "ENDPOINT", "page-customers", "endpoint-customer-get", null);
        verify(api).check(caller, "FIELD", "page-customers", "endpoint-customer-get", "field-name");
        verifyNoMoreInteractions(api);
    }

    @Test void rejectsIdentityOverridesUrlsAndUnknownArgumentsBeforeCallingApi() {
        for (String key : List.of("userId", "subject", "issuer", "url", "headers", "method")) {
            assertInvalid(() -> tools.listMyPages(Map.of(key, "anything")));
            assertInvalid(() -> tools.checkMyContextAccess(Map.of("targetType", "PAGE", "pageId", "p", key, "anything")));
        }
        verifyNoInteractions(api);
    }

    @Test void rejectsWrongLimitTypesOverflowAndNullOrMalformedCursor() {
        for (Object value : List.of(0, 6, -1, 1.0, true, "1", new BigInteger("18446744073709551617"))) {
            assertInvalid(() -> tools.listMyPages(Map.of("limit", value)));
        }
        for (String cursor : List.of("", " ", "a/b", "a?x=1", "한글", "x".repeat(65))) {
            assertInvalid(() -> tools.listMyPages(Map.of("after", cursor)));
        }
        Map<String, Object> nullLimit = new HashMap<>(); nullLimit.put("limit", null);
        Map<String, Object> nullCursor = new HashMap<>(); nullCursor.put("after", null);
        assertInvalid(() -> tools.listMyPages(nullLimit));
        assertInvalid(() -> tools.listMyPages(nullCursor));
        assertInvalid(() -> tools.listMyPages(null));
        verifyNoInteractions(api);
    }

    @Test void enforcesExactTargetHierarchyAndFieldDirection() {
        List<Map<String, Object>> malformed = List.of(
                Map.of("targetType", "FIELD", "pageId", "p", "fieldId", "f"),
                Map.of("targetType", "ENDPOINT", "pageId", "p"),
                Map.of("targetType", "ENDPOINT", "pageId", "p", "endpointId", "e", "fieldId", "f"),
                Map.of("targetType", "PAGE", "pageId", "p", "endpointId", "e"),
                Map.of("targetType", "page", "pageId", "p"),
                Map.of("targetType", "PAGE", "pageId", 1),
                Map.of("targetType", "FIELD", "pageId", "p", "endpointId", "../e", "fieldId", "f"));
        malformed.forEach(arguments -> assertInvalid(() -> tools.checkMyContextAccess(arguments)));
        assertInvalid(() -> tools.listMyEndpointFields(Map.of("pageId", "p", "endpointId", "e")));
        assertInvalid(() -> tools.listMyEndpointFields(Map.of("pageId", "p", "endpointId", "e", "direction", "response")));
        verifyNoInteractions(api);
    }

    @Test void registersBoundedReadOnlySchemasWithoutChangingLegacyNames() {
        var specifications = new ContextToolRegistration().contextToolSpecifications(tools, new com.fasterxml.jackson.databind.ObjectMapper());
        assertThat(specifications).extracting(specification -> specification.tool().name())
                .containsExactly("list_my_pages", "list_my_page_endpoints", "list_my_endpoint_fields", "check_my_context_access");
        for (var specification : specifications) {
            var tool = specification.tool();
            assertThat(tool.inputSchema().additionalProperties()).isFalse();
            assertThat(tool.annotations().readOnlyHint()).isTrue();
            assertThat(tool.annotations().destructiveHint()).isFalse();
            assertThat(tool.annotations().idempotentHint()).isTrue();
            assertThat(tool.inputSchema().properties()).doesNotContainKeys("userId", "subject", "url");
        }
        assertThat(specifications.getFirst().tool().inputSchema().properties().get("limit"))
                .isEqualTo(Map.of("type", "integer", "minimum", 1, "maximum", 5, "default", 5));
    }

    private static void assertInvalid(Runnable action) {
        assertThatThrownBy(action::run).isInstanceOf(IllegalArgumentException.class).hasMessage("INVALID_ARGUMENT");
    }
}
