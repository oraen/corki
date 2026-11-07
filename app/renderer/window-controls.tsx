import React, {useEffect, useState} from "react";
import {Icon} from "./components";

export function WindowControls({startup = false, beforeClose, onError}: {
  startup?: boolean;
  beforeClose?: () => Promise<void>;
  onError: (error: unknown) => void;
}) {
  const api = window.corki;
  const windows = api.platform === "win32";
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    if (!windows) return;
    let current = true;
    const update = (state: {maximized: boolean}) => {if (current) setMaximized(state.maximized);};
    const unsubscribe = api.onWindowState?.(update);
    void api.windowState?.().then(update).catch(onError);
    return () => {current = false; unsubscribe?.();};
  }, [windows]);
  if (!windows) return null;
  const act = async (action: "minimize" | "maximize" | "close") => {
    try {
      if (action === "close") await beforeClose?.();
      const state = await api.windowControl?.(action);
      if (state) setMaximized(state.maximized);
    } catch (error) {onError(error);}
  };
  return <div className={`window-controls${startup ? " window-controls-startup" : ""}`} role="group" aria-label="窗口控制">
    <button type="button" title="最小化" aria-label="最小化" onClick={() => void act("minimize")}>
      <Icon name="minimize" size={17}/>
    </button>
    <button type="button" title={maximized ? "还原窗口" : "最大化"} aria-label={maximized ? "还原窗口" : "最大化"} onClick={() => void act("maximize")}>
      <Icon name={maximized ? "restore" : "maximize"} size={17}/>
    </button>
    <button type="button" className="window-close" title="关闭窗口" aria-label="关闭窗口" onClick={() => void act("close")}>
      <Icon name="close" size={17}/>
    </button>
  </div>;
}
