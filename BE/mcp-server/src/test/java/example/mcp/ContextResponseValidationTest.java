package example.mcp;

import java.util.ArrayList;
import java.util.List;
import example.contract.ContextAccessResponses.*;
import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.*;

class ContextResponseValidationTest {
    private static final Grant GRANT = new Grant("role-analyst", "policy-page");
    private static Stage allowed(String stage) { return new Stage(stage, true, List.of(GRANT), false); }
    private static Stage denied(String stage) { return new Stage(stage, false, List.of(), false); }

    @Test void acceptsOrderedPagesAndEnforcesExclusiveCursorAndBoundedContinuation() {
        var first = new Page("page-a", "app", "/a", "페이지 A");
        var second = new Page("page-b", "app", "/b", "페이지 B");
        assertThatCode(() -> ContextResponseValidation.pages(new PageList(List.of(first, second), "page-b"), null, 2)).doesNotThrowAnyException();
        assertThatCode(() -> ContextResponseValidation.pages(new PageList(List.of(second), null), "page-a", 2)).doesNotThrowAnyException();
        invalid(() -> ContextResponseValidation.pages(new PageList(List.of(first), null), "page-a", 5));
        invalid(() -> ContextResponseValidation.pages(new PageList(List.of(first, first), null), null, 5));
        invalid(() -> ContextResponseValidation.pages(new PageList(List.of(second, first), null), null, 5));
        invalid(() -> ContextResponseValidation.pages(new PageList(List.of(first), "page-b"), null, 1));
        invalid(() -> ContextResponseValidation.pages(new PageList(List.of(first), "page-a"), null, 5));
        invalid(() -> ContextResponseValidation.pages(new PageList(List.of(first, second), null), null, 1));
    }

    @Test void rejectsEndpointAndFieldMetadataFromAnotherContextOrDirection() {
        var endpoint = new Endpoint("endpoint-customer", "customer-api", "GET", "/customers/{id}", "고객 조회");
        assertThatCode(() -> ContextResponseValidation.endpoints(new EndpointList("page-a", List.of(endpoint), null), "page-a", null, 5)).doesNotThrowAnyException();
        invalid(() -> ContextResponseValidation.endpoints(new EndpointList("page-b", List.of(endpoint), null), "page-a", null, 5));
        var field = new Field("field-name", "endpoint-customer", "RESPONSE", "BODY", "items[].name", "이름");
        assertThatCode(() -> ContextResponseValidation.fields(new FieldList("page-a", "endpoint-customer", "RESPONSE", List.of(field), null), "page-a", "endpoint-customer", "RESPONSE", null, 5)).doesNotThrowAnyException();
        invalid(() -> ContextResponseValidation.fields(new FieldList("page-a", "endpoint-other", "RESPONSE", List.of(field), null), "page-a", "endpoint-customer", "RESPONSE", null, 5));
        invalid(() -> ContextResponseValidation.fields(new FieldList("page-a", "endpoint-customer", "REQUEST", List.of(field), null), "page-a", "endpoint-customer", "REQUEST", null, 5));
        var invalidLocation = new Field("field-name", "endpoint-customer", "RESPONSE", "QUERY", "name", "이름");
        invalid(() -> ContextResponseValidation.fields(new FieldList("page-a", "endpoint-customer", "RESPONSE", List.of(invalidLocation), null), "page-a", "endpoint-customer", "RESPONSE", null, 5));
    }

    @Test void acceptsOnlyCompleteAllowOrFirstFailureDecisionWithMatchingTargets() {
        var all = List.of(allowed("PAGE"), allowed("ENDPOINT"), allowed("FIELD"));
        assertThatCode(() -> ContextResponseValidation.decision(decision(true, "ALLOWED", all), "FIELD", "p", "e", "f")).doesNotThrowAnyException();
        assertThatCode(() -> ContextResponseValidation.decision(decision(false, "ENDPOINT_NOT_GRANTED", List.of(allowed("PAGE"), denied("ENDPOINT"))), "FIELD", "p", "e", "f")).doesNotThrowAnyException();
        invalid(() -> ContextResponseValidation.decision(decision(true, "ALLOWED", List.of(allowed("PAGE"))), "FIELD", "p", "e", "f"));
        invalid(() -> ContextResponseValidation.decision(decision(false, "FIELD_NOT_GRANTED", all), "FIELD", "p", "e", "f"));
        invalid(() -> ContextResponseValidation.decision(decision(false, "PAGE_NOT_GRANTED", List.of(denied("PAGE"), allowed("ENDPOINT"))), "FIELD", "p", "e", "f"));
        invalid(() -> ContextResponseValidation.decision(decision(false, "FIELD_NOT_GRANTED", List.of(allowed("PAGE"), denied("ENDPOINT"))), "FIELD", "p", "e", "f"));
        invalid(() -> ContextResponseValidation.decision(decision(true, "ALLOWED", all), "FIELD", "other-page", "e", "f"));
        invalid(() -> ContextResponseValidation.decision(new Decision("PAGE", "p", "e", null, true, "ALLOWED", List.of(allowed("PAGE"))), "PAGE", "p", null, null));
    }

    @Test void rejectsDenialProvenanceAndContradictoryOrDuplicateGrantEvidence() {
        invalid(() -> ContextResponseValidation.decision(decision(false, "PAGE_NOT_GRANTED", List.of(new Stage("PAGE", false, List.of(GRANT), false))), "FIELD", "p", "e", "f"));
        invalid(() -> ContextResponseValidation.decision(decision(false, "PAGE_NOT_GRANTED", List.of(new Stage("PAGE", false, List.of(), true))), "FIELD", "p", "e", "f"));
        for (Stage invalid : List.of(new Stage("PAGE", true, List.of(), false),
                new Stage("PAGE", true, List.of(GRANT, GRANT), false),
                new Stage("PAGE", true, List.of(GRANT), true),
                new Stage("PAGE", true, List.of(new Grant("role/bad", "policy")), false))) {
            invalid(() -> ContextResponseValidation.decision(new Decision("PAGE", "p", null, null, true, "ALLOWED", List.of(invalid)), "PAGE", "p", null, null));
        }
    }

    @Test void acceptsExplicitTruncationAndKeepsMaximumValidPayloadBelowApiByteLimit() throws Exception {
        var grants = new ArrayList<Grant>();
        for (int index = 0; index < 5; index++) grants.add(new Grant("r".repeat(63) + index, "p".repeat(64)));
        var stages = List.of(new Stage("PAGE", true, grants, true), new Stage("ENDPOINT", true, grants, true), new Stage("FIELD", true, grants, true));
        var result = decision(true, "ALLOWED", stages);
        assertThatCode(() -> ContextResponseValidation.decision(result, "FIELD", "p", "e", "f")).doesNotThrowAnyException();
        var fields = new ArrayList<Field>();
        for (int index = 0; index < 5; index++) fields.add(new Field("f".repeat(63) + index, "e".repeat(64), "RESPONSE", "BODY", "\\".repeat(200), "한".repeat(80)));
        var list = new FieldList("p".repeat(64), "e".repeat(64), "RESPONSE", fields, fields.getLast().id());
        ContextResponseValidation.fields(list, list.pageId(), list.endpointId(), "RESPONSE", null, 5);
        var json = new com.fasterxml.jackson.databind.ObjectMapper();
        assertThat(json.writeValueAsBytes(list).length).isLessThan(16384);
        assertThat(json.writeValueAsBytes(result).length).isLessThan(16384);
    }

    @Test void rejectsOutOfContractMetadataAndNullStructures() {
        for (Page page : List.of(new Page("p", "app", "https://elsewhere", "Page"),
                new Page("p", "app", "/a?secret=1", "Page"), new Page("p", "app", "/a", "x".repeat(81)),
                new Page("p", "app", "/a", "line\nbreak"), new Page("p", "app", "/한글", "Page"))) {
            invalid(() -> ContextResponseValidation.pages(new PageList(List.of(page), null), null, 5));
        }
        invalid(() -> ContextResponseValidation.pages(new PageList(null, null), null, 5));
        invalid(() -> ContextResponseValidation.pages(null, null, 5));
        invalid(() -> ContextResponseValidation.decision(decision(false, "PAGE_NOT_GRANTED", null), "FIELD", "p", "e", "f"));
    }

    private static Decision decision(boolean allowed, String reason, List<Stage> stages) {
        return new Decision("FIELD", "p", "e", "f", allowed, reason, stages);
    }

    private static void invalid(Runnable action) {
        assertThatThrownBy(action::run).isInstanceOf(AccessApiClient.ApiFailure.class).hasMessage("UPSTREAM_INVALID_RESPONSE");
    }
}
