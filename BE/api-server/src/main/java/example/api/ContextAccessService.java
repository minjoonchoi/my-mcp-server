package example.api;

import org.springframework.stereotype.Service;
import example.contract.ContextAccessResponses.*;

@Service
public class ContextAccessService {
    private final ContextAccessRepository repository;

    public ContextAccessService(ContextAccessRepository repository) { this.repository = repository; }

    public PageList pages(String issuer, String subject, String after, int limit) {
        return engine(issuer, subject).pages(after, limit);
    }

    public EndpointList endpoints(String issuer, String subject, String pageId, String after, int limit) {
        return engine(issuer, subject).endpoints(pageId, after, limit);
    }

    public FieldList fields(String issuer, String subject, String pageId, String endpointId, String direction, String after, int limit) {
        return engine(issuer, subject).fields(pageId, endpointId, direction, after, limit);
    }

    public Decision check(String issuer, String subject, String type, String pageId, String endpointId, String fieldId) {
        return engine(issuer, subject).check(type, pageId, endpointId, fieldId);
    }

    private ContextAccessEngine engine(String issuer, String subject) {
        return new ContextAccessEngine(repository.snapshot(issuer, subject));
    }
}
