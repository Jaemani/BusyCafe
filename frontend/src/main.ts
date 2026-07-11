import "./style.css";

const app = document.querySelector<HTMLElement>("#app");

if (!app) {
  throw new Error("App root was not found");
}
