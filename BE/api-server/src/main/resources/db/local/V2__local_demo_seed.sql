-- Local profile only. Flyway history ensures one-time insertion; no truncate or reseeding.
INSERT INTO users VALUES
('alice','https://demo-issuer.invalid','alice','앨리스','분석 및 편집 담당자'),
('bob','https://demo-issuer.invalid','bob','밥','청구 조회 담당자'),
('carol','https://demo-issuer.invalid','carol','캐럴','역할이 아직 없는 사용자');
INSERT INTO roles VALUES
('role-analyst','분석가','보고서와 분석 정보 조회'),
('role-editor','편집자','편집 자료 접근'),
('role-billing','청구 담당','청구 정보와 공용 보고서 조회');
INSERT INTO policies VALUES
('policy-reports','분석 조회','보고서와 대시보드 접근 허용'),
('policy-shared','공용 보고서','공용 보고서 접근 허용'),
('policy-edit','편집 자료','대시보드와 문서 접근 허용'),
('policy-billing','청구 조회','청구 내역 접근 허용');
INSERT INTO resources VALUES
('R100','공용 보고서','조직의 분석 보고서'),
('R200','분석 대시보드','분석 결과 화면'),
('R300','편집 문서','편집 담당 자료'),
('R400','청구 내역','청구 담당 자료');
INSERT INTO user_roles VALUES ('alice','role-analyst'),('alice','role-editor'),('bob','role-billing');
INSERT INTO role_policies VALUES
('role-analyst','policy-reports'),('role-analyst','policy-shared'),
('role-editor','policy-edit'),('role-editor','policy-shared'),
('role-billing','policy-billing'),('role-billing','policy-shared');
INSERT INTO policy_resources VALUES
('policy-reports','R100'),('policy-reports','R200'),('policy-shared','R100'),
('policy-edit','R200'),('policy-edit','R300'),('policy-billing','R400');
