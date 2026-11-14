package example.api;

interface ContextAccessRepository {
    ContextSnapshot snapshot(String originalIssuer, String subject);
}
