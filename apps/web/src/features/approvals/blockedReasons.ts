/** Explanations for `decide_blocked_reason`/`apply_blocked_reason`
 * (app/domain/approvals/service.py: SELF_APPROVAL, MISSING_PERMISSION, STALE,
 * EXPIRED, WRONG_STATUS), shown instead of the Approve/Reject/Apply buttons. */
export const BLOCKED_REASON_TEXT: Record<string, string> = {
  SELF_APPROVAL: 'You proposed this analysis. Another supervisor must approve it.',
  MISSING_PERMISSION: 'Your role does not include permission to approve or reject recommendations.',
  STALE: 'The inputs this proposal was computed from have changed.',
  EXPIRED: 'This proposal has expired.',
  WRONG_STATUS: 'This recommendation is not awaiting a decision.',
}
