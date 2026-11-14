package example.mcp;

import java.util.List;
import example.contract.AccessResponses.*;
import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.*;

class AccessResponseValidationTest {
    @Test
    void acceptsIndependentLocalUserIdFromTheAuthenticatedApi() {
        // A verified subject such as "alice" may legitimately map to local id "u1".
        // The original subject is intentionally absent from this response-only validation API.
        var response = new MyAccess(new User("u1", "Alice", "Local account", List.of()),
                List.of(), List.of(), List.of());
        assertThatCode(() -> AccessResponseValidation.myAccess(response)).doesNotThrowAnyException();
    }

    @Test
    void rejectsGrantProvenanceOutsideTheReturnedUserRoles() {
        var response = new MyAccess(new User("u1", "Alice", "Local account", List.of("role-own")),
                List.of(new Role("role-own", "Own role", "Assigned", List.of("policy-own"))),
                List.of(new Policy("policy-own", "Own policy", "Assigned", List.of("R100"), List.of("role-other"))),
                List.of(new GrantedResource("R100", "Resource", "Shared resource",
                        List.of(new Grant("role-other", "policy-own")))));
        assertThatThrownBy(() -> AccessResponseValidation.myAccess(response))
                .isInstanceOf(AccessApiClient.ApiFailure.class).hasMessage("UPSTREAM_INVALID_RESPONSE");
    }
}
