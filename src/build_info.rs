//! Build identity helpers.

pub const BASE_VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn channel() -> &'static str {
    non_empty(option_env!("HERDR_BUILD_CHANNEL")).unwrap_or("stable")
}

pub fn build_id() -> Option<&'static str> {
    non_empty(option_env!("HERDR_BUILD_ID"))
}

pub fn version() -> String {
    match channel() {
        "stable" => BASE_VERSION.to_string(),
        channel => match build_id() {
            Some(build_id) => format!("{BASE_VERSION}-{channel}.{build_id}"),
            None => format!("{BASE_VERSION}-{channel}"),
        },
    }
}

pub fn is_preview() -> bool {
    channel() == "preview"
}

/// Followcat builds retain the upstream protocol and use their own release feed.
pub fn is_custom() -> bool {
    channel() == "followcat"
}

pub fn update_manifest_url(upstream: &'static str) -> &'static str {
    if is_custom() {
        "https://github.com/followcat/herdr/releases/latest/download/update.json"
    } else {
        upstream
    }
}

pub fn agent_manifest_catalog_url(upstream: &str) -> String {
    if is_custom() {
        let revision = non_empty(option_env!("HERDR_BUILD_COMMIT")).unwrap_or("followcat");
        format!("https://raw.githubusercontent.com/followcat/herdr/{revision}/distribution/agent-detection/index.toml")
    } else {
        upstream.to_string()
    }
}

fn non_empty(value: Option<&'static str>) -> Option<&'static str> {
    value.and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed)
        }
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn stable_version_defaults_to_cargo_version() {
        assert!(!super::version().is_empty());
    }
}
