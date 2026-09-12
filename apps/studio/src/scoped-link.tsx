import { Link as RouterLink, useLocation, type LinkProps } from "react-router-dom";

export function Link({ to, ...props }: LinkProps) {
  const location = useLocation();
  const environment = new URLSearchParams(location.search).get("environment");
  if (environment && typeof to === "string" && to.startsWith("/")) {
    const url = new URL(to, "http://studio.local");
    if (!url.searchParams.has("environment")) url.searchParams.set("environment", environment);
    to = `${url.pathname}${url.search}${url.hash}`;
  }
  return <RouterLink to={to} {...props} />;
}
