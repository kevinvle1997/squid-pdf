// A style may set a CSS variable, as `style={{ "--sheet-width": "816px" }}`, without a cast.
import "react";

declare module "react" {
  interface CSSProperties {
    [variable: `--${string}`]: string | number | undefined;
  }
}
