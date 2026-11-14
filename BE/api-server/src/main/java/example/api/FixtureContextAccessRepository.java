package example.api;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Repository;

@Repository
@Profile("fixture")
class FixtureContextAccessRepository implements ContextAccessRepository {
    private final AccessDirectory directory;

    FixtureContextAccessRepository(AccessDirectory directory) { this.directory = directory; }

    @Override
    public ContextSnapshot snapshot(String originalIssuer, String subject) {
        return directory.contextSnapshot(originalIssuer, subject);
    }
}
