//! Plugin registry — discovers and manages TOML-based plugins.

use crate::client::bridge::VirtuosoClient;
use crate::error::{Result, VirtuosoError};
use crate::mcp::tools::McpTool;
use crate::rpc::schema::{Method, Param};
use serde_json::Value;
use std::path::PathBuf;
use std::sync::OnceLock;

use super::schema::PluginTool;

/// Global plugin registry, discovered once at startup.
static PLUGIN_REGISTRY: OnceLock<PluginRegistry> = OnceLock::new();

/// Plugin registry — holds all discovered plugin tools.
#[derive(Debug)]
pub struct PluginRegistry {
    tools: Vec<PluginTool>,
}

impl PluginRegistry {
    /// Discover and load all plugins from `~/.config/vcli/plugins/*.toml`.
    pub fn discover() -> Result<Self> {
        let config_dir = crate::runtime_paths::config_subdir(&["plugins"]);

        if !config_dir.exists() {
            return Ok(PluginRegistry { tools: Vec::new() });
        }

        let mut tools = Vec::new();

        let entries = std::fs::read_dir(&config_dir)
            .map_err(|e| VirtuosoError::Execution(format!("failed to read plugins dir: {e}")))?;

        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) == Some("toml") {
                match Self::load_plugin(&path) {
                    Ok(mut plugin_tools) => tools.append(&mut plugin_tools),
                    Err(e) => {
                        tracing::warn!("failed to load plugin {:?}: {}", path, e);
                    }
                }
            }
        }

        tracing::debug!("discovered {} plugin tools", tools.len());
        Ok(PluginRegistry { tools })
    }

    /// Load a single plugin TOML file.
    fn load_plugin(path: &PathBuf) -> Result<Vec<PluginTool>> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| VirtuosoError::Execution(format!("failed to read {:?}: {e}", path)))?;

        if let Ok(single) = toml::from_str::<PluginTool>(&content) {
            single.validate()?;
            return Ok(vec![single]);
        }

        let plugins: Vec<PluginTool> = toml::from_str(&content)
            .map_err(|e| VirtuosoError::Execution(format!("failed to parse {:?}: {e}", path)))?;

        for plugin in &plugins {
            plugin.validate()?;
        }

        Ok(plugins)
    }

    /// Get the global plugin registry, initializing it if not yet loaded.
    pub fn get_global() -> Result<&'static PluginRegistry> {
        Ok(PLUGIN_REGISTRY.get_or_init(|| {
            Self::discover().expect("plugin discovery should not fail at this point")
        }))
    }

    /// Dispatch a plugin tool call.
    pub fn owns_domain(&self, domain: &str) -> bool {
        self.tools.iter().any(|t| t.domain == domain)
    }

    pub fn dispatch(
        &self,
        domain: &str,
        op: &str,
        params: Value,
        client: &VirtuosoClient,
        caps: &crate::capability::CapabilitySet,
    ) -> Result<Value> {
        if !caps.allows_raw_skill() {
            return Err(VirtuosoError::Execution(format!(
                "plugin method '{domain}.{op}' renders raw SKILL and needs the admin capability"
            )));
        }
        let tool = self
            .tools
            .iter()
            .find(|t| t.domain == domain && t.name == op)
            .ok_or_else(|| {
                VirtuosoError::Execution(format!("unknown plugin method '{}.{}'", domain, op))
            })?;

        let args = params
            .as_object()
            .ok_or_else(|| VirtuosoError::Execution("params must be an object".into()))?;

        let skill = tool.render_skill(args)?;
        let result = client
            .execute_skill_with_bypass(&skill, None, false)?
            .ok_or_exec(&format!("plugin {}.{}", domain, op))?;

        if result.output.trim().is_empty() {
            Ok(Value::Null)
        } else {
            match serde_json::from_str(&result.output) {
                Ok(v) => Ok(v),
                Err(_) => Ok(Value::String(result.output.trim().to_string())),
            }
        }
    }

    /// Convert plugin tools to MCP tools.
    pub fn mcp_tools(&self) -> Vec<McpTool> {
        self.tools
            .iter()
            .map(|t| {
                let domain = t.rpc_method().split('.').next().unwrap_or("").into();
                McpTool::new(
                    t.mcp_name(),
                    t.description.clone(),
                    t.input_schema(),
                    t.rpc_method(),
                    domain,
                )
            })
            .collect()
    }

    /// Convert plugin tools to RPC schema methods.
    #[allow(dead_code)]
    pub fn schema_methods(&self) -> Vec<Method> {
        self.tools
            .iter()
            .map(|t| Method {
                name: t.rpc_method(),
                summary: t.description.clone(),
                params: t
                    .params
                    .iter()
                    .map(|(name, def)| Param {
                        name: name.clone(),
                        ptype: def.ptype.clone(),
                        description: def.description.clone(),
                        required: def.required,
                    })
                    .collect(),
                returns: "any".into(),
            })
            .collect()
    }
}

#[cfg(test)]
mod gate_tests {
    use super::*;
    use crate::capability::{Capability, CapabilitySet};

    #[test]
    fn plugin_dispatch_needs_admin_before_anything_is_sent() {
        let registry = PluginRegistry { tools: Vec::new() };
        let client = VirtuosoClient::new("127.0.0.1", 1, 1);
        let no_admin = CapabilitySet::from_capabilities(&[Capability::Schematic]);
        let err = registry
            .dispatch("myplugin", "op", serde_json::json!({}), &client, &no_admin)
            .unwrap_err()
            .to_string();
        assert!(err.contains("admin capability"), "{err}");
        let admin = CapabilitySet::from_capabilities(&[Capability::Admin]);
        let err = registry
            .dispatch("myplugin", "op", serde_json::json!({}), &client, &admin)
            .unwrap_err()
            .to_string();
        assert!(err.contains("unknown plugin method"), "{err}");
        assert!(!registry.owns_domain("myplugin"));
    }
}
