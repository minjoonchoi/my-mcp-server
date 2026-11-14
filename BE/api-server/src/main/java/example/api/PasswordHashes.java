package example.api;

import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Base64;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/** Fixed-cost, versioned hashes. Stored values cannot choose the amount of password work. */
public final class PasswordHashes {
    private static final String PREFIX = "pbkdf2-sha256$600000$";
    private static final Base64.Encoder ENCODER = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder DECODER = Base64.getUrlDecoder();
    private PasswordHashes() {}

    public static String hash(String password) {
        byte[] salt = new byte[16];
        new SecureRandom().nextBytes(salt);
        return PREFIX + ENCODER.encodeToString(salt) + "$" + ENCODER.encodeToString(derive(password, salt));
    }

    public static boolean valid(String encoded) {
        if (encoded == null || !encoded.matches("pbkdf2-sha256\\$600000\\$[A-Za-z0-9_-]{22}\\$[A-Za-z0-9_-]{43}")) return false;
        String[] parts = encoded.split("\\$", -1);
        try {
            byte[] salt = DECODER.decode(parts[2]), hash = DECODER.decode(parts[3]);
            return salt.length == 16 && hash.length == 32
                    && ENCODER.encodeToString(salt).equals(parts[2]) && ENCODER.encodeToString(hash).equals(parts[3]);
        } catch (IllegalArgumentException invalid) { return false; }
    }

    public static boolean matches(String password, String encoded) {
        if (!valid(encoded)) return false;
        String[] parts = encoded.split("\\$", -1);
        byte[] actual = derive(password, DECODER.decode(parts[2]));
        try { return MessageDigest.isEqual(DECODER.decode(parts[3]), actual); }
        finally { Arrays.fill(actual, (byte) 0); }
    }

    private static byte[] derive(String password, byte[] salt) {
        char[] chars = password.toCharArray();
        var spec = new PBEKeySpec(chars, salt, 600000, 256);
        Arrays.fill(chars, '\0');
        try { return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded(); }
        catch (java.security.GeneralSecurityException unavailable) { throw new IllegalStateException("Password verification unavailable"); }
        finally { spec.clearPassword(); }
    }
}
