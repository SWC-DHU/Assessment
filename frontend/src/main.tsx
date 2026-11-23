import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#4e745b",
          colorSuccess: "#52785b",
          colorWarning: "#9b702d",
          colorError: "#a24d45",
          colorBgLayout: "#f8f9f7",
          colorBgContainer: "#ffffff",
          borderRadius: 8,
          borderRadiusLG: 8,
          fontSize: 14,
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          Layout: {
            headerBg: "#ffffff",
            headerHeight: 56,
            headerPadding: "0 16px",
            bodyBg: "#f8f9f7",
          },
          Table: {
            cellPaddingBlockSM: 7,
            cellPaddingInlineSM: 10,
            headerBg: "#fafafa",
          },
          Tabs: {
            horizontalMargin: "0",
          },
          Card: {
            paddingSM: 12,
          },
        },
      }}
    >
      <AntdApp>
        <App />
      </AntdApp>
    </ConfigProvider>
  </StrictMode>
);
