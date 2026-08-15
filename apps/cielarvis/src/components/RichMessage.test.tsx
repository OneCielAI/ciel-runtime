import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RichMessage } from "./RichMessage";

describe("RichMessage", () => {
  it("renders complete GFM, safe HTML, and inline images", () => {
    const html = renderToStaticMarkup(<RichMessage endpoint="http://127.0.0.1:6970" message={{
      id: 1,
      message: "**complete**\n\n|a|b|\n|-|-|\n|1|2|\n\n<div>HTML preview</div>\n\n![chart](https://example.test/chart.png)\n\n<script>alert(1)</script>",
    }} />);
    expect(html).toContain("<strong>complete</strong>");
    expect(html).toContain("<table>");
    expect(html).toContain("HTML preview");
    expect(html).toContain("chart.png");
    expect(html).not.toContain("<script");
  });

  it("previews image and sandboxed HTML attachments", () => {
    const html = renderToStaticMarkup(<RichMessage endpoint="http://127.0.0.1:6970" message={{
      id: 2,
      message: "Files",
      meta: { attachments: [
        { original_name: "plot.png", path: "/ca/chat/files/plot.png", content_type: "image/png" },
        { original_name: "report.html", path: "/ca/chat/files/report.html", content_type: "text/html" },
      ] },
    }} />);
    expect(html).toContain("http://127.0.0.1:6970/ca/chat/files/plot.png");
    expect(html).toContain("<iframe");
    expect(html).toContain('sandbox=""');
  });
});
