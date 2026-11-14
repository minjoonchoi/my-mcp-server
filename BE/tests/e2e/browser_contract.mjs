// Positive rollback claims must not match warnings such as "롤백이 아닙니다".
export function claimsRollbackCompleted(text) {
  return /롤백(?:이|은|을)?\s*(?:완료(?:되었습니다|됐습니다|했습니다|됨)?(?:[.!。]|$)|되었습니다|됐습니다|했습니다)|되돌렸습니다|되돌려졌습니다|변경되지 않았습니다|취소되어 적용되지 않았/.test(text);
}

export function grantPathTuple(text) {
  const parts = text.trim().split(/\s+→\s+/u);
  if (parts.length !== 4) throw new Error('Expected exactly four grant path nodes');
  return ['사용자', '역할', '정책', '리소스'].map((label, index) => {
    const identifier = index === 3 ? 'R[0-9]{3}' : '[A-Za-z0-9_-]{1,64}';
    const match = new RegExp('^' + label + ' .+ \\((' + identifier + ')\\)$', 'u').exec(parts[index]);
    if (!match) throw new Error('Malformed grant path node: ' + label);
    return match[1];
  });
}
