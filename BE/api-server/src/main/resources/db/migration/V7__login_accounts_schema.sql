-- Credentials are provisioned explicitly after schema migration, never seeded or reset by Flyway.
CREATE TABLE login_accounts (
    username VARBINARY(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_login_user UNIQUE (user_id),
    CONSTRAINT fk_login_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
