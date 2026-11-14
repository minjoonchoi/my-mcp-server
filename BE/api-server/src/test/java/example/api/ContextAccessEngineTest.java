package example.api;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import com.fasterxml.jackson.databind.ObjectMapper;
import example.contract.ContextAccessResponses.*;
import org.junit.jupiter.api.Test;
import org.springframework.util.LinkedMultiValueMap;

import static org.junit.jupiter.api.Assertions.*;

class ContextAccessEngineTest {
    private static final String ISSUER = "https://demo-issuer.invalid";
    private static final String PAGE = ContextFixtures.CUSTOMERS;
    private static final String AUDIT = ContextFixtures.AUDIT;
    private static final String ENDPOINT = ContextFixtures.ENDPOINT;
    private static final String EMAIL = "field-customer-email-response";

    private static ContextAccessEngine engine(AccessDirectory directory, String user) {
        return new ContextAccessEngine(directory.contextSnapshot(ISSUER, user));
    }

    @Test
    void pageContextAndCrossRoleUnionAreBothRequired() {
        var engine = engine(new AccessDirectory(), "alice");
        Decision allowed = engine.check("FIELD", AUDIT, ENDPOINT, EMAIL);
        assertTrue(allowed.allowed());
        assertEquals(List.of("PAGE", "ENDPOINT", "FIELD"), allowed.stages().stream().map(Stage::stage).toList());
        assertEquals("role-editor", allowed.stages().getFirst().grants().getFirst().roleId());
        assertEquals("role-analyst", allowed.stages().getLast().grants().getFirst().roleId());
        Decision denied = engine.check("FIELD", PAGE, ENDPOINT, EMAIL);
        assertFalse(denied.allowed());
        assertEquals("FIELD_NOT_GRANTED", denied.reason());
        assertEquals(List.of(), denied.stages().getLast().grants());
        assertEquals(List.of("field-customer-name-response"),
                engine.fields(PAGE, ENDPOINT, "RESPONSE", null, 5).items().stream().map(Field::id).toList());
        assertEquals(List.of("field-customer-email-response", "field-customer-name-response"),
                engine.fields(AUDIT, ENDPOINT, "RESPONSE", null, 5).items().stream().map(Field::id).toList());
    }

    @Test
    void noRolesAndAdministratorRoleDoNotGrantImplicitTypedAccess() {
        var directory = new AccessDirectory();
        for (String user : List.of("bob", "carol", "admin", "local-id-demo")) {
            var engine = engine(directory, user);
            assertEquals(List.of(), engine.pages(null, 5).items());
            Decision denied = engine.check("FIELD", AUDIT, ENDPOINT, EMAIL);
            assertEquals("PAGE_NOT_GRANTED", denied.reason());
            assertEquals(List.of(new Stage("PAGE", false, List.of(), false)), denied.stages());
        }
        assertEquals(403, assertThrows(ApiProblem.class,
                () -> directory.contextSnapshot(ISSUER, "ALICE")).status);
    }

    @Test
    void unknownAndDeniedTargetsHaveTheSameEvidenceAndReason() {
        var bob = engine(new AccessDirectory(), "bob");
        Decision hidden = bob.check("PAGE", PAGE, null, null);
        Decision absent = bob.check("PAGE", "page-missing", null, null);
        assertEquals(hidden.reason(), absent.reason());
        assertEquals(hidden.stages(), absent.stages());
        assertEquals("NOT_FOUND", assertThrows(ApiProblem.class, () -> bob.endpoints(PAGE, null, 5)).getMessage());
        assertEquals("NOT_FOUND", assertThrows(ApiProblem.class, () -> bob.endpoints("page-missing", null, 5)).getMessage());
        var alice = engine(new AccessDirectory(), "alice");
        Decision deniedField = alice.check("FIELD", PAGE, ENDPOINT, EMAIL);
        Decision absentField = alice.check("FIELD", PAGE, ENDPOINT, "field-missing");
        assertEquals(deniedField.reason(), absentField.reason());
        assertEquals(deniedField.stages(), absentField.stages());
    }

    @Test
    void registeredBindingAndFieldOwnershipCannotBeReplacedByGrants() {
        ContextSnapshot snapshot = new AccessDirectory().contextSnapshot(ISSUER, "alice");
        var noBinding = new ContextAccessEngine(new ContextSnapshot(snapshot.pages(), snapshot.endpoints(),
                snapshot.fields(), Set.of(), snapshot.rules()));
        Decision denied = noBinding.check("FIELD", AUDIT, ENDPOINT, EMAIL);
        assertEquals("ENDPOINT_NOT_GRANTED", denied.reason());
        assertEquals(2, denied.stages().size());
        var fields = new HashMap<>(snapshot.fields());
        fields.put(EMAIL, new Field(EMAIL, "other-endpoint", "RESPONSE", "BODY", "email", "Email"));
        var wrongOwner = new ContextAccessEngine(new ContextSnapshot(snapshot.pages(), snapshot.endpoints(),
                fields, snapshot.bindings(), snapshot.rules()));
        assertEquals("FIELD_NOT_GRANTED", wrongOwner.check("FIELD", AUDIT, ENDPOINT, EMAIL).reason());
    }

    @Test
    void requestAndResponseFieldsRemainSeparate() {
        var engine = engine(new AccessDirectory(), "alice");
        assertEquals(List.of("field-customer-id-request"), engine.fields(PAGE, ENDPOINT, "REQUEST", null, 5)
                .items().stream().map(Field::id).toList());
        assertFalse(engine.fields(PAGE, ENDPOINT, "RESPONSE", null, 5).items().stream()
                .anyMatch(f -> f.id().equals("field-customer-id-request")));
        assertThrows(ApiProblem.class, () -> engine.fields(PAGE, ENDPOINT, "request", null, 5));
    }

    @Test
    void auditedRelationshipChangesAffectTheNextFixtureSnapshot() {
        var directory = new AccessDirectory();
        var relation = Map.of("roleId", "role-editor", "policyId", "ctx-page-audit");
        directory.change(ISSUER, "admin", "detach_policy_from_role", relation);
        assertEquals("PAGE_NOT_GRANTED", engine(directory, "alice").check("FIELD", AUDIT, ENDPOINT, EMAIL).reason());
        directory.change(ISSUER, "admin", "attach_policy_to_role", relation);
        assertTrue(engine(directory, "alice").check("FIELD", AUDIT, ENDPOINT, EMAIL).allowed());
        directory.change(ISSUER, "admin", "assign_role_to_user", Map.of("userId", "carol", "roleId", "role-analyst"));
        assertTrue(engine(directory, "carol").check("PAGE", PAGE, null, null).allowed());
        assertFalse(engine(directory, "carol").check("FIELD", AUDIT, ENDPOINT, EMAIL).allowed());
        directory.change(ISSUER, "admin", "remove_role_from_user", Map.of("userId", "carol", "roleId", "role-analyst"));
        assertEquals(List.of(), engine(directory, "carol").pages(null, 5).items());
    }

    @Test
    void contextualPolicyCannotAcquireLegacyResourceLinks() {
        var directory = new AccessDirectory();
        var targets = Map.of("policyId", "ctx-page-customers", "resourceId", "R100");
        for (String operation : List.of("attach_resource_to_policy", "detach_resource_from_policy")) {
            assertEquals(400, assertThrows(ApiProblem.class,
                    () -> directory.change(ISSUER, "admin", operation, targets)).status);
            assertEquals(403, assertThrows(ApiProblem.class,
                    () -> directory.change(ISSUER, "alice", operation, targets)).status);
        }
        assertEquals(List.of("R100", "R200", "R300"), directory.access(ISSUER, "alice").resources().stream()
                .map(example.contract.AccessResponses.GrantedResource::id).toList());
    }

    @Test
    void keysetPagingSkipsInaccessiblePagesWithoutSkippingAccessibleItems() {
        Map<String, Page> pages = new HashMap<>();
        List<ContextSnapshot.Rule> rules = new ArrayList<>();
        for (int i = 0; i < 12; i++) {
            String id = "page-" + String.format("%02d", i);
            pages.put(id, new Page(id, "backoffice", "/" + id, id));
            if (i % 2 == 0) rules.add(new ContextSnapshot.Rule("PAGE", id, null, null, new Grant("reader", "policy-" + i)));
        }
        var engine = new ContextAccessEngine(new ContextSnapshot(pages, Map.of(), Map.of(), Set.of(), rules));
        PageList first = engine.pages(null, 5);
        assertEquals(List.of("page-00", "page-02", "page-04", "page-06", "page-08"), first.items().stream().map(Page::id).toList());
        assertEquals("page-08", first.nextCursor());
        PageList second = engine.pages(first.nextCursor(), 5);
        assertEquals(List.of("page-10"), second.items().stream().map(Page::id).toList());
        assertNull(second.nextCursor());
        assertEquals(List.of(), engine.pages("page-99", 5).items());
    }

    @Test
    void provenanceIsDeterministicDeduplicatedAndExplicitlyTruncated() {
        List<ContextSnapshot.Rule> rules = new ArrayList<>();
        for (int i = 9; i >= 0; i--) rules.add(new ContextSnapshot.Rule("PAGE", PAGE, null, null, new Grant("role-" + i, "policy-" + i)));
        rules.add(rules.getFirst());
        var snapshot = new ContextSnapshot(Map.of(PAGE, new Page(PAGE, "app", "/customers", "Customers")),
                Map.of(), Map.of(), Set.of(), rules);
        Stage stage = new ContextAccessEngine(snapshot).check("PAGE", PAGE, null, null).stages().getFirst();
        assertTrue(stage.allowed());
        assertTrue(stage.grantsTruncated());
        assertEquals(List.of("role-0", "role-1", "role-2", "role-3", "role-4"), stage.grants().stream().map(Grant::roleId).toList());
    }

    @Test
    void invalidArgumentsAndDuplicateQueryParametersAreRejected() {
        var engine = engine(new AccessDirectory(), "alice");
        assertThrows(ApiProblem.class, () -> engine.check("PAGE", PAGE, ENDPOINT, null));
        assertThrows(ApiProblem.class, () -> engine.check("ENDPOINT", PAGE, null, null));
        assertThrows(ApiProblem.class, () -> engine.check("FIELD", PAGE, ENDPOINT, null));
        assertThrows(ApiProblem.class, () -> engine.check("FIELD", "../page", ENDPOINT, EMAIL));
        assertThrows(ApiProblem.class, () -> engine.pages("", 5));
        assertThrows(ApiProblem.class, () -> engine.pages(null, 6));
        var duplicate = new LinkedMultiValueMap<String, String>();
        duplicate.add("after", PAGE); duplicate.add("after", AUDIT);
        assertThrows(ApiProblem.class, () -> ContextAccessController.values(duplicate, Set.of("after"), Set.of()));
        var spoof = new LinkedMultiValueMap<String, String>();
        spoof.add("userId", "alice");
        assertThrows(ApiProblem.class, () -> ContextAccessController.values(spoof, Set.of("after"), Set.of()));
        assertThrows(ApiProblem.class, () -> ContextAccessController.values(new LinkedMultiValueMap<>(), Set.of("direction"), Set.of("direction")));
    }

    @Test
    void boundedMetadataKeepsDiscoveryBelowTheDownstreamByteBudget() throws Exception {
        List<Field> fields = new ArrayList<>();
        for (int i = 0; i < 5; i++) fields.add(new Field("f".repeat(63) + i, "e".repeat(64), "RESPONSE", "BODY",
                "\\".repeat(200), "한".repeat(80)));
        byte[] bytes = new ObjectMapper().writeValueAsBytes(new FieldList("p".repeat(64), "e".repeat(64), "RESPONSE", fields, "f".repeat(64)));
        assertTrue(bytes.length < 16384);
        assertThrows(ApiProblem.class, () -> new ContextSnapshot(Map.of(PAGE, new Page(PAGE, "app", "/bad?query", "Customers")),
                Map.of(), Map.of(), Set.of(), List.of()));
        assertThrows(ApiProblem.class, () -> new ContextSnapshot(Map.of(PAGE, new Page(PAGE, "app", "/customers", "x".repeat(81))),
                Map.of(), Map.of(), Set.of(), List.of()));
        assertThrows(ApiProblem.class, () -> new ContextSnapshot(Map.of(), Map.of(),
                Map.of("blank-field", new Field("blank-field", ENDPOINT, "RESPONSE", "BODY", "   ", "Blank field")),
                Set.of(), List.of()));
    }
}
