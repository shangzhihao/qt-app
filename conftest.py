pytest_plugins = ["pytester"]


def pytest_report_header():
    """Ground truth for the analysis: today's daily quality factor per topic."""
    from qt_app.answer import daily_factor, graph_factors

    return "qt daily factors: " + ", ".join(
        f"{topic} {daily_factor(topic):.3f}" for topic in sorted(graph_factors())
    )
