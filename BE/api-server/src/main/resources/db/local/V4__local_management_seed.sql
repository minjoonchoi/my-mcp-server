INSERT INTO users VALUES
('U900','https://demo-issuer.invalid','local-id-demo','독립 ID 사용자','subject와 로컬 ID가 다른 무역할 사용자'),
('admin','https://demo-issuer.invalid','admin','로컬 관리자','개발용 보호 시스템 역할 보유자');
INSERT INTO roles (id,name,description,protected,requestable) VALUES
('SYSTEM_ADMIN','시스템 관리자','DB 관리 권한의 보호된 시작점',TRUE,FALSE);
INSERT INTO policies (id,name,description,protected) VALUES
('SYSTEM_POLICY','시스템 정책','보호된 시스템 정책',TRUE);
INSERT INTO resources (id,name,description,protected) VALUES
('R900','시스템 리소스','보호된 시스템 리소스',TRUE);
INSERT INTO user_roles VALUES ('admin','SYSTEM_ADMIN');
INSERT INTO role_policies VALUES ('SYSTEM_ADMIN','SYSTEM_POLICY');
INSERT INTO policy_resources VALUES ('SYSTEM_POLICY','R900');
UPDATE roles SET requestable=TRUE WHERE id IN ('role-analyst','role-editor','role-billing');
