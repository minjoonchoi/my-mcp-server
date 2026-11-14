ALTER TABLE roles ADD COLUMN protected BOOLEAN NOT NULL DEFAULT FALSE,
                  ADD COLUMN requestable BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE policies ADD COLUMN protected BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE resources ADD COLUMN protected BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE access_audit (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    actor_issuer VARBINARY(1024) NOT NULL,
    actor_subject VARBINARY(512) NOT NULL,
    actor_user_id VARCHAR(64) NOT NULL,
    operation VARCHAR(64) NOT NULL,
    left_id VARCHAR(64) NOT NULL,
    right_id VARCHAR(64) NOT NULL,
    occurred_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    success BOOLEAN NOT NULL,
    changed BOOLEAN NOT NULL,
    KEY ix_audit_actor_time (actor_user_id, occurred_at),
    FOREIGN KEY (actor_user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
