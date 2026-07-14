"""jg research — scaffold + open project research notes.

Closes the write-back loop from the CLI: jg (mechanical) creates a dated,
frontmatter'd file in the project's research dir; Claude (inferential) fills it
in. Next time you open the project workspace, `list_research` resurfaces it.
"""

from __future__ import annotations

import datetime as dt

import click
from rich.console import Console

from jg import projectdocs
from jg.config import Config, Project
from jg.tmux import quote_for_shell, spawn_in_dir

console = Console()
err = Console(stderr=True)


@click.command()
@click.argument("project_name")
@click.argument("topic", nargs=-1)
@click.option("-e", "--edit", is_flag=True, help="Open the note in your editor instead of Claude")
@click.pass_context
def research(ctx: click.Context, project_name: str, topic: tuple[str, ...], edit: bool) -> None:
    """Project research notes.

    \b
    jg research <project>            list existing research
    jg research <project> <topic>    scaffold a dated note + open Claude to fill it
    """
    config: Config = ctx.obj["config"]
    proj = config.project_by_name(project_name)
    if proj is None:
        known = ", ".join(p.name for p in config.projects) or "(none configured)"
        err.print(f"[red]✗[/] no project named '{project_name}'. Known: {known}")
        ctx.exit(1)

    topic_str = " ".join(topic).strip()
    if not topic_str:
        _list(proj)
        return

    path = projectdocs.new_research_file(proj, topic_str)
    base = projectdocs.project_base(proj)
    cwd = str(base) if base and base.is_dir() else str(path.parent)

    if edit:
        editor = config.ui.editor_command or "nvim"
        cmd = f"{editor} {quote_for_shell(str(path))}"
    else:
        prompt = (
            f"Research: {topic_str}. Project: {proj.name}. Write your findings into "
            f"{path} (the frontmatter is already set — fill in the body). Be thorough "
            f"and cite sources."
        )
        cmd = f"{config.ai.claude_path} {quote_for_shell(prompt)}"

    try:
        spawn_in_dir(cmd, cwd=cwd, title=f"research·{proj.name}", config=config.tmux)
    except RuntimeError as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    console.print(f"[green]✓[/] {path}")


def _list(proj: Project) -> None:
    docs = projectdocs.list_research(proj)
    rpath = projectdocs.research_path(proj)
    if not docs:
        console.print(f"[dim]no research yet in {rpath}[/]")
        console.print(f'[dim]jg research "{proj.name}" "<topic>" to start[/]')
        return
    console.print(f"[bold]Research · {proj.name}[/]  [dim]{rpath}[/]")
    for d in docs:
        date = dt.datetime.fromtimestamp(d.mtime).strftime("%Y-%m-%d") if d.mtime else "—"
        console.print(f"  [cyan]{date}[/]  {d.title}  [dim]{d.path.name}[/]")
