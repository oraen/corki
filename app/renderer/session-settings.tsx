import React, {useRef, useState} from "react";
import {Icon, Modal, Picker} from "./components";
import type {Catalog, SessionSettings} from "../shared/protocol";
import type {Draft} from "./state";

export function resolveSessionDraft(draft: Draft, catalog: Catalog): Draft {
  if (draft.sessionSettings === undefined || !catalog.valid) return draft;
  const options = {...draft.sessionSettings};
  let provider = catalog.providers.find(p => p.id === (options.provider || catalog.default?.provider));
  if (!provider) {delete options.provider;delete options.model;delete options.reasoning;delete options.contextWindow;provider = catalog.providers.find(p => p.id === catalog.default?.provider);}
  const defaultId = options.provider ? provider?.defaultModel : catalog.default?.model;
  let model = provider?.models.find(m => m.id === (options.model || defaultId));
  if (!model) {delete options.model;delete options.reasoning;delete options.contextWindow;model = provider?.models.find(m => m.id === defaultId) || provider?.models[0];}
  if (options.reasoning && !model?.reasoningLevels.includes(options.reasoning)) delete options.reasoning;
  return {...draft, sessionSettings: options, provider: provider?.id || "", model: model?.id || "",
    reasoning: options.reasoning || model?.reasoning || null};
}

export function SessionSettingsDialog({catalog, draft, threadId, title, blocked, onSave, onClose}: {
  title: string;
  catalog: Catalog; draft: Draft; threadId: string; blocked: boolean;
  onSave: (settings: SessionSettings, effective: {provider: string; model: string; reasoning: string | null}, name: string) => Promise<void>;
  onClose: () => void;
}) {
  const initial = draft.sessionSettings ?? {provider: draft.provider, model: draft.model,
    ...(draft.reasoning ? {reasoning: draft.reasoning} : {})};
  const [values, setValues] = useState<SessionSettings>(initial);
  const [name, setName] = useState(threadId === "new" ? draft.sessionTitle || "" : title);
  const [context, setContext] = useState(initial.contextWindow ? String(initial.contextWindow / 1024) : "");
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  const savingRef = useRef(false);
  const defaults = catalog.default;
  const provider = catalog.providers.find(p => p.id === (values.provider || defaults?.provider));
  const defaultModel = provider?.models.find(m => m.id === (values.provider ? provider.defaultModel : defaults?.model)) || provider?.models[0];
  const selectedModel = provider?.models.find(m => m.id === values.model) || defaultModel;
  const disabled = blocked || saving;
  const change = (key: keyof SessionSettings, value: unknown) => {
    setValues(previous => {
      const next = {...previous};
      if (value === undefined) delete next[key]; else Object.assign(next, {[key]: value});
      if (key === "provider" || key === "model") {
        if (key === "provider") delete next.model;
        delete next.reasoning; delete next.contextWindow; setContext("");
      }
      return next;
    });
    setError("");
  };
  const select = (label: string, key: keyof SessionSettings, options: {id: string; label: string}[], defaultLabel = "跟随全局") =>
    <div className="session-setting-field"><span>{label}</span><Picker label={label} placement="auto"
      value={values[key] === undefined ? "" : String(values[key])}
      disabled={disabled || (key === "reasoning" && !selectedModel?.reasoningLevels.length)}
      onChange={value => change(key, value === "" ? undefined :
        ["memories", "skills"].includes(key) ? value === "true" : value)}
      items={[{id:"",label:defaultLabel}, ...options]} />
    </div>;
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (savingRef.current || blocked) return;
    savingRef.current = true; setSaving(true); setError("");
    try {
      if (threadId !== "new" && !name.trim()) throw new Error("会话名称不能为空");
      const settings = {...values};
      if (context.trim()) {
        const number = Number(context);
        if (!Number.isInteger(number) || number < 1 || number > 16384) throw new Error("上下文窗口请输入 1 到 16384 之间的整数");
        settings.contextWindow = number * 1024;
      } else delete settings.contextWindow;
      const effective = await window.corki.request("session.validate", {
        ...(threadId !== "new" ? {threadId} : {}), directory: draft.directory, sessionSettings: settings,
      });
      await onSave(settings, effective, name.trim());
      onClose();
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { savingRef.current = false; setSaving(false); }
  }
  return <Modal className="session-settings" title={<span className="session-settings-heading"><Icon name="gear" size={18}/><span>会话设置</span></span>} onClose={() => {if (!savingRef.current) onClose();}}>
    <form onSubmit={event => void save(event)}>
      <label className="session-setting-field session-name-field"><span>会话名称</span>
        <input aria-label="会话名称" maxLength={120} value={name} placeholder="新会话" disabled={disabled}
          onChange={event => {setName(event.target.value);setError("");}} />
      </label>
      <fieldset disabled={disabled}>
        <legend>模型</legend><div className="session-settings-grid">
          {select("供应商", "provider", catalog.providers.map(p => ({id: p.id, label: p.id})), `跟随全局${defaults?.provider ? ` · ${defaults.provider}` : ""}`)}
          {select("模型", "model", (provider?.models || []).map(m => ({id: m.id, label: m.id})), `默认${defaultModel ? ` · ${defaultModel.id}` : ""}`)}
          {select("推理等级", "reasoning", (selectedModel?.reasoningLevels || []).map(id => ({id, label: id})), selectedModel?.reasoningLevels.length ? "模型默认" : "不支持设置")}
          <label className="session-setting-field"><span>上下文窗口</span><div className="session-context-input">
            <input aria-label="上下文窗口" type="number" min="1" max={Math.min(16384, Math.floor((selectedModel?.maxContextWindow || 16777216) / 1024))}
              step="1" value={context} placeholder={selectedModel?.contextWindow ? `默认 ${Math.round(selectedModel.contextWindow / 1024)}K` : "跟随模型"}
              onChange={event => {setContext(event.target.value);setError("");}} /><span>K</span>
          </div></label>
        </div>
      </fieldset>
      <fieldset disabled={disabled}><legend>行为</legend><div className="session-settings-grid">
        {select("工作模式", "mode", [{id:"default",label:"执行"},{id:"plan",label:"规划"}])}
        {select("操作权限", "permissions", [{id:"read-only",label:"只读"},{id:"workspace",label:"工作区"},{id:"full",label:"完全访问"}]
          .filter(option => catalog.permissionModes?.includes(option.id)))}
        {select("长期记忆", "memories", [{id:"true",label:"开启"},{id:"false",label:"关闭"}])}
        {select("Skills", "skills", [{id:"true",label:"开启"},{id:"false",label:"关闭"}])}
      </div></fieldset>
      {(error || blocked) && <p className="session-settings-error" role="alert">{error || "请先停止当前任务再修改设置"}</p>}
      <footer><button type="button" className="session-settings-reset" disabled={disabled} onClick={() => {setValues({});setContext("");setError("");}}>恢复全局设置</button>
        <button type="button" disabled={saving} onClick={onClose}>取消</button>
        <button type="submit" className="primary" disabled={disabled}>{saving ? "保存中…" : "保存"}</button>
      </footer>
    </form>
  </Modal>;
}
