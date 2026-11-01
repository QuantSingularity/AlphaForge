import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Card, Empty, ErrorState, Loading, Metric } from "./ui";

describe("Card", () => {
  it("renders its children", () => {
    render(
      <Card>
        <p>inside</p>
      </Card>,
    );

    expect(screen.getByText("inside")).toBeInTheDocument();
  });

  it("shows a title only when one is given", () => {
    const { container, rerender } = render(<Card>body</Card>);
    expect(container.querySelector(".card-title")).toBeNull();

    rerender(<Card title="Positions">body</Card>);
    expect(screen.getByText("Positions")).toHaveClass("card-title");
  });

  it("applies inline styles to the card", () => {
    const { container } = render(<Card style={{ minHeight: 120 }}>body</Card>);

    expect(container.firstElementChild).toHaveStyle({ minHeight: "120px" });
  });
});

describe("Metric", () => {
  it("renders the label and the value", () => {
    render(<Metric label="Sharpe" value="1.42" />);

    expect(screen.getByText("Sharpe")).toHaveClass("label");
    expect(screen.getByText("1.42")).toHaveClass("value");
  });

  it("omits the sub line when none is given", () => {
    const { container } = render(<Metric label="VaR" value="2%" />);

    expect(container.querySelector(".sub")).toBeNull();
  });

  it("shows the sub line when given", () => {
    render(<Metric label="VaR" value="2%" sub="95% confidence" />);

    expect(screen.getByText("95% confidence")).toHaveClass("sub");
  });

  it.each([
    ["up", "up"],
    ["down", "down"],
  ] as const)("colours the value for the %s tone", (tone, expectedClass) => {
    render(<Metric label="P&L" value="1%" tone={tone} />);

    expect(screen.getByText("1%")).toHaveClass("value", expectedClass);
  });

  it("adds no tone class by default", () => {
    render(<Metric label="P&L" value="1%" />);

    expect(screen.getByText("1%").className).toBe("value");
  });
});

describe("Loading, ErrorState and Empty", () => {
  it("Loading defaults to a plain loading message", () => {
    render(<Loading />);

    expect(screen.getByText("Loading...")).toHaveClass("loading");
  });

  it("Loading accepts a custom label", () => {
    render(<Loading label="Running backtest" />);

    expect(screen.getByText("Running backtest...")).toBeInTheDocument();
  });

  it("ErrorState shows the message in an error toast", () => {
    render(<ErrorState message="request failed (500)" />);

    expect(screen.getByText("request failed (500)")).toHaveClass(
      "toast",
      "err",
    );
  });

  it("Empty shows its children", () => {
    render(<Empty>No positions yet</Empty>);

    expect(screen.getByText("No positions yet")).toHaveClass("empty");
  });
});
