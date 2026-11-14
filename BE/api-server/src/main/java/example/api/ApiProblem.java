package example.api;

/** Fixed public error codes only; never holds SQL or request data. */
public final class ApiProblem extends RuntimeException {
    public final int status;
    public ApiProblem(int status, String code) { super(code, null, false, false); this.status = status; }
}
