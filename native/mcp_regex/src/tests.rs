use super::*;

#[test]
fn later_alternative_can_match_whole_value() {
    let regex = full(r"https://api\.example\.com|https://api\.example\.com/mcp").unwrap();
    assert!(regex.is_match("https://api.example.com/mcp"));
    assert!(!regex.is_match("https://api.example.com/mcp/other"));
}

#[test]
fn original_and_wrapper_are_separate_validation_steps() {
    let expression = "(?x)mcp # trailing comment";
    assert!(Regex::new(expression).is_ok());
    assert!(full(expression).is_err());
    assert_eq!(
        unsafe { validate(expression.as_ptr(), expression.len()) },
        2
    );
}

#[test]
fn bridge_preserves_ascii_classes_and_codepoint_literals() {
    assert!(!full(r"\d+").unwrap().is_match("١٢٣"));
    assert!(!full(r"(?i)k").unwrap().is_match("K"));
    assert!(full(r"[^β]").unwrap().is_match("雪"));
    let expression = r"[[:alpha:]]+";
    let candidate = "abc";
    assert_eq!(
        unsafe {
            matches(
                expression.as_ptr(),
                expression.len(),
                candidate.as_ptr(),
                candidate.len(),
            )
        },
        1
    );
}
