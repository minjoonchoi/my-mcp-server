package example.api;

/** Trusted account-to-domain identity mapping. Authorization state is read afresh on login. */
public interface LoginAccountRepository {
    record Account(String userId, String issuer, String subject, String name,
                   String passwordHash, boolean enabled, boolean administrator) {
        @Override public String toString() { return "Account[credentials=<redacted>]"; }
    }
    Account find(String username);
}
