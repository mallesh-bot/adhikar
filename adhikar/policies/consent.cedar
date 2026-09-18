// Adhikar consent policy.
// Wired into Strands' native CedarAuthorization intervention handler,
// which gates the search_schemes tool call and builds the request context
// as {input: <tool args>, session: {...}} automatically.
//
// Verified against cedarpy 4.8.7:
//   - no sensitive_attributes                          -> allowed
//   - a sensitive attribute, nothing in session.consented -> denied
//   - the same attribute, now in session.consented        -> allowed

permit(principal, action, resource);

forbid(principal, action == Action::"search_schemes", resource)
when {
    context.input.sensitive_attributes.containsAny(["income_band", "disability_status", "caste_category"])
}
unless {
    context.session.consented.containsAll(context.input.sensitive_attributes)
};
