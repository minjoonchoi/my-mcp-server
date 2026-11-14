package example.security;

import java.io.ByteArrayInputStream;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import org.springframework.http.MediaType;

/** Runs before authentication on the two explicit anonymous login endpoints only. */
final class LoginRequestBoundary {
    private LoginRequestBoundary() {}

    static void filter(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        response.setHeader("Cache-Control", "no-store");
        boolean json;
        try {
            MediaType type = MediaType.parseMediaType(request.getContentType() == null ? "" : request.getContentType());
            json = "application".equals(type.getType()) && "json".equals(type.getSubtype());
        } catch (IllegalArgumentException invalid) { json = false; }
        if (!json || request.getContentLengthLong() > LoginProtocol.MAX_REQUEST_BYTES) {
            invalid(response); return;
        }
        byte[] body = request.getInputStream().readNBytes(LoginProtocol.MAX_REQUEST_BYTES + 1);
        if (body.length > LoginProtocol.MAX_REQUEST_BYTES) { invalid(response); return; }
        chain.doFilter(new HttpServletRequestWrapper(request) {
            @Override public ServletInputStream getInputStream() {
                var input = new ByteArrayInputStream(body);
                return new ServletInputStream() {
                    @Override public int read() { return input.read(); }
                    @Override public boolean isFinished() { return input.available() == 0; }
                    @Override public boolean isReady() { return true; }
                    @Override public void setReadListener(ReadListener listener) {
                        throw new UnsupportedOperationException("Synchronous login only");
                    }
                };
            }
            @Override public BufferedReader getReader() {
                return new BufferedReader(new InputStreamReader(getInputStream(), StandardCharsets.UTF_8));
            }
        }, response);
    }

    private static void invalid(HttpServletResponse response) throws IOException {
        response.setStatus(400);
        response.setContentType("application/json");
        response.getOutputStream().write("{\"code\":\"INVALID_ARGUMENT\"}".getBytes(StandardCharsets.UTF_8));
    }
}
