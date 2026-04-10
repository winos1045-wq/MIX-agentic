"""
AI Project Planning Tool
Reads necessary files → Thinks through codebase → Generates execution plan → Saves to plans/
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.tree import Tree
from rich.text import Text

console = Console()


def plan_project(
    working_directory: str,
    task_description: str,
    file_patterns: Optional[List[str]] = None,
    max_files: int = 50,
    max_file_size: int = 100000,  # 100KB per file
    include_dependencies: bool = True,
    save_plan: bool = True,
    show_live: bool = True
) -> str:
    """
    Intelligent project planning tool with 3-phase approach:
    1. READ: Scan and read relevant project files
    2. THINK: Analyze architecture, patterns, complexity
    3. PLAN: Generate step-by-step implementation plan
    
    Args:
        working_directory: Root directory of the project to analyze
        task_description: What you want to accomplish (e.g., "Add user authentication")
        file_patterns: List of glob patterns (e.g., ["*.py", "*.js"]). Auto-detected if None
        max_files: Maximum number of files to analyze (default: 50)
        max_file_size: Maximum size per file in bytes (default: 100KB)
        include_dependencies: Whether to read package.json, requirements.txt, etc.
        save_plan: Whether to save plan to plans/ directory
        show_live: Show live progress and output
    
    Returns:
        Detailed execution plan with file analysis, approach, and implementation steps
    """
    
    abs_working_dir = os.path.abspath(working_directory)
    
    # Validate directory
    if not os.path.isdir(abs_working_dir):
        return f'Error: Working directory {working_directory} does not exist'
    
    if show_live:
        console.print()
        console.print(Panel(
            f"[cyan]🎯 Planning Task: {task_description}[/cyan]\n[yellow]Directory: {abs_working_dir}[/yellow]",
            title="[bold blue]🧠 AI Project Planner[/bold blue]",
            border_style="blue",
            padding=(1, 2)
        ))
    
    try:
        # ====================================================================
        # PHASE 1: READ - Scan and read relevant project files
        # ====================================================================
        if show_live:
            console.print("\n[bold cyan]Phase 1/3: Reading Project Files[/bold cyan]")
        
        files_data = _read_project_files(
            abs_working_dir,
            file_patterns,
            max_files,
            max_file_size,
            include_dependencies,
            show_live
        )
        
        # ====================================================================
        # PHASE 2: THINK - Analyze codebase structure and complexity
        # ====================================================================
        if show_live:
            console.print("\n[bold cyan]Phase 2/3: Analyzing Codebase[/bold cyan]")
        
        analysis = _analyze_codebase(
            files_data,
            task_description,
            show_live
        )
        
        # ====================================================================
        # PHASE 3: PLAN - Generate detailed execution plan
        # ====================================================================
        if show_live:
            console.print("\n[bold cyan]Phase 3/3: Generating Execution Plan[/bold cyan]")
        
        plan = _generate_plan(
            files_data,
            analysis,
            task_description,
            show_live
        )
        
        # Format final output
        final_output = _format_detailed_plan(
            task_description,
            abs_working_dir,
            files_data,
            analysis,
            plan
        )
        
        # Save plan to plans/ directory
        plan_file_path = None
        if save_plan:
            plan_file_path = _save_plan_to_file(
                task_description,
                final_output,
                abs_working_dir
            )
            
            if show_live and plan_file_path:
                console.print(f"\n[green]✓ Plan saved to: {plan_file_path}[/green]")
        
        if show_live:
            console.print("\n[green]✓ Planning complete! Ready to execute.[/green]\n")
        
        return final_output
    
    except Exception as e:
        error_msg = f'Error during planning: {str(e)}'
        if show_live:
            console.print(f"[red]{error_msg}[/red]\n")
        return error_msg


# ============================================================================
# PHASE 1: READ PROJECT FILES
# ============================================================================

def _read_project_files(
    working_dir: str,
    file_patterns: Optional[List[str]],
    max_files: int,
    max_file_size: int,
    include_dependencies: bool,
    show_live: bool
) -> Dict:
    """
    Phase 1: Intelligently read project files.
    
    Returns:
        {
            'files': [{'path': str, 'content': str, 'type': str, 'size': int}],
            'structure': str,  # Directory tree
            'dependencies': dict,
            'metadata': dict
        }
    """
    
    result = {
        'files': [],
        'structure': '',
        'dependencies': {},
        'metadata': {
            'total_files': 0,
            'total_size': 0,
            'project_type': None
        }
    }
    
    # Auto-detect project type
    project_type = _detect_project_type(working_dir)
    result['metadata']['project_type'] = project_type
    
    # Auto-generate patterns if not provided
    if file_patterns is None:
        file_patterns = _get_default_patterns(project_type)
    
    # Priority files (always read these first)
    priority_files = [
        'README.md', 'package.json', 'requirements.txt',
        'setup.py', 'pyproject.toml', 'Cargo.toml',
        '.env.example', 'config.py', 'settings.py',
        'tsconfig.json', 'next.config.js', 'vite.config.js'
    ]
    
    files_read = 0
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console if show_live else None,
        transient=True
    ) as progress:
        task = progress.add_task(description="Scanning files...", total=None)
        
        # Read priority files first
        for priority_file in priority_files:
            priority_path = os.path.join(working_dir, priority_file)
            if os.path.exists(priority_path):
                file_data = _read_file(priority_path, max_file_size)
                if file_data:
                    result['files'].append(file_data)
                    files_read += 1
                    
                    # Store dependencies separately
                    if priority_file in ['package.json', 'requirements.txt', 'Cargo.toml', 'pyproject.toml']:
                        result['dependencies'][priority_file] = file_data['content']
        
        # Read matching pattern files
        for pattern in file_patterns:
            if files_read >= max_files:
                break
            
            for file_path in Path(working_dir).rglob(pattern):
                if files_read >= max_files:
                    break
                
                # Skip irrelevant paths
                if _should_skip_path(str(file_path)):
                    continue
                
                file_data = _read_file(str(file_path), max_file_size)
                if file_data:
                    result['files'].append(file_data)
                    files_read += 1
                    result['metadata']['total_size'] += file_data['size']
    
    result['metadata']['total_files'] = files_read
    
    # Build directory tree structure
    result['structure'] = _build_tree_structure(working_dir, result['files'])
    
    if show_live:
        console.print(f"  [green]✓ Read {files_read} files ({result['metadata']['total_size'] / 1024:.1f} KB)[/green]")
        console.print(f"  [cyan]Project Type: {project_type}[/cyan]")
    
    return result


def _detect_project_type(working_dir: str) -> str:
    """Detect project type from indicator files."""
    indicators = {
        'nextjs': ['next.config.js', 'next.config.mjs', 'next.config.ts'],
        'react': ['package.json'],  # Check for react in deps later
        'vue': ['vue.config.js', 'nuxt.config.js'],
        'python-django': ['manage.py', 'wsgi.py'],
        'python-fastapi': ['main.py'],  # Check for fastapi imports
        'python': ['requirements.txt', 'setup.py', 'pyproject.toml'],
        'rust': ['Cargo.toml'],
        'go': ['go.mod'],
        'node': ['package.json']
    }
    
    for project_type, files in indicators.items():
        for file in files:
            if os.path.exists(os.path.join(working_dir, file)):
                # Special check for React vs Next.js
                if project_type == 'react' and file == 'package.json':
                    try:
                        with open(os.path.join(working_dir, file), 'r') as f:
                            pkg = json.load(f)
                            if 'next' in pkg.get('dependencies', {}):
                                return 'nextjs'
                            if 'react' in pkg.get('dependencies', {}):
                                return 'react'
                    except:
                        pass
                return project_type
    
    return 'unknown'


def _get_default_patterns(project_type: str) -> List[str]:
    """Get file patterns based on project type."""
    patterns = {
        'nextjs': ['*.tsx', '*.ts', '*.jsx', '*.js', 'app/**/*.tsx', 'pages/**/*.tsx', 'components/**/*.tsx'],
        'react': ['*.tsx', '*.ts', '*.jsx', '*.js', 'src/**/*.tsx'],
        'vue': ['*.vue', '*.ts', '*.js'],
        'python-django': ['*.py', 'urls.py', 'views.py', 'models.py', 'settings.py'],
        'python-fastapi': ['*.py', 'main.py', 'routers/*.py'],
        'python': ['*.py'],
        'rust': ['*.rs', 'src/**/*.rs'],
        'go': ['*.go', 'cmd/**/*.go'],
        'node': ['*.js', '*.ts']
    }
    
    return patterns.get(project_type, ['*.*'])


def _should_skip_path(path: str) -> bool:
    """Check if path should be skipped."""
    skip_patterns = [
        'node_modules', '__pycache__', '.git', 'venv', 'env',
        'dist', 'build', '.next', 'target', '.pytest_cache',
        'coverage', '.venv', 'venv', '.env', '.mypy_cache',
        '.tox', '.eggs', '*.egg-info', '.cargo', 'vendor'
    ]
    
    return any(pattern in path for pattern in skip_patterns)


def _read_file(file_path: str, max_size: int) -> Optional[Dict]:
    """Read a single file safely."""
    try:
        stat = os.stat(file_path)
        if stat.st_size > max_size:
            return None
        
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        return {
            'path': file_path,
            'content': content,
            'type': Path(file_path).suffix,
            'size': stat.st_size
        }
    except Exception:
        return None


def _build_tree_structure(working_dir: str, files: List[Dict]) -> str:
    """Build a tree structure representation of read files."""
    tree_lines = []
    tree_lines.append(f"Project Root: {working_dir}")
    
    # Group files by directory
    dirs = {}
    for file_data in files:
        rel_path = os.path.relpath(file_data['path'], working_dir)
        parts = Path(rel_path).parts
        
        if len(parts) == 1:
            # Root level file
            if 'root' not in dirs:
                dirs['root'] = []
            dirs['root'].append(parts[0])
        else:
            # Nested file
            dir_name = parts[0]
            if dir_name not in dirs:
                dirs[dir_name] = []
            dirs[dir_name].append('/'.join(parts[1:]))
    
    # Build tree
    for i, (dir_name, file_list) in enumerate(sorted(dirs.items())):
        is_last_dir = i == len(dirs) - 1
        prefix = "└── " if is_last_dir else "├── "
        
        if dir_name == 'root':
            for j, fname in enumerate(sorted(file_list)):
                is_last = j == len(file_list) - 1 and is_last_dir
                tree_lines.append(f"{'└── ' if is_last else '├── '}{fname}")
        else:
            tree_lines.append(f"{prefix}{dir_name}/")
            for j, fname in enumerate(sorted(file_list)):
                is_last = j == len(file_list) - 1
                file_prefix = "    └── " if is_last else "    ├── "
                tree_lines.append(f"{file_prefix}{fname}")
    
    return '\n'.join(tree_lines)


# ============================================================================
# PHASE 2: ANALYZE CODEBASE
# ============================================================================

def _analyze_codebase(
    files_data: Dict,
    task_description: str,
    show_live: bool
) -> Dict:
    """
    Phase 2: Analyze architecture, patterns, and complexity.
    
    Returns:
        {
            'architecture': str,
            'relevant_files': List[str],
            'existing_patterns': List[str],
            'technology_stack': List[str],
            'complexity_assessment': str,
            'key_insights': List[str]
        }
    """
    
    analysis = {
        'architecture': 'Unknown',
        'relevant_files': [],
        'existing_patterns': [],
        'technology_stack': [],
        'complexity_assessment': 'Medium',
        'key_insights': []
    }
    
    # Detect architecture
    analysis['architecture'] = _detect_architecture(files_data)
    
    # Find files relevant to task
    analysis['relevant_files'] = _find_relevant_files(files_data, task_description)
    
    # Identify technology stack
    analysis['technology_stack'] = _identify_tech_stack(files_data)
    
    # Find existing code patterns
    analysis['existing_patterns'] = _find_code_patterns(files_data)
    
    # Assess complexity
    analysis['complexity_assessment'] = _assess_complexity(
        files_data,
        task_description,
        analysis['relevant_files']
    )
    
    # Generate key insights
    analysis['key_insights'] = _generate_insights(files_data, task_description)
    
    if show_live:
        console.print(f"  [green]✓ Architecture: {analysis['architecture']}[/green]")
        console.print(f"  [cyan]Relevant Files: {len(analysis['relevant_files'])}[/cyan]")
        console.print(f"  [yellow]Complexity: {analysis['complexity_assessment']}[/yellow]")
    
    return analysis


def _detect_architecture(files_data: Dict) -> str:
    """Detect architectural pattern from files."""
    project_type = files_data['metadata']['project_type']
    
    # Check for common patterns
    file_paths = [f['path'] for f in files_data['files']]
    
    if any('pages' in path or 'app' in path for path in file_paths):
        if project_type == 'nextjs':
            return 'Next.js App Router / Pages Router'
    
    if any('models' in path and 'views' in path for path in file_paths):
        return 'MVC (Model-View-Controller)'
    
    if any('routers' in path or 'routes' in path for path in file_paths):
        return 'API Routes / Router-based'
    
    if any('components' in path for path in file_paths):
        return 'Component-based Architecture'
    
    return 'Modular / Standard Structure'


def _find_relevant_files(files_data: Dict, task_description: str) -> List[str]:
    """Find files most relevant to the task."""
    relevant = []
    task_lower = task_description.lower()
    
    # Keywords to look for in task description
    keywords = task_lower.split()
    
    for file_data in files_data['files']:
        file_path = file_data['path'].lower()
        file_content = file_data['content'].lower()
        
        # Check if file path or content contains task keywords
        relevance_score = 0
        for keyword in keywords:
            if keyword in file_path:
                relevance_score += 2
            if keyword in file_content:
                relevance_score += 1
        
        if relevance_score > 0:
            relevant.append({
                'path': file_data['path'],
                'score': relevance_score
            })
    
    # Sort by relevance and return top files
    relevant.sort(key=lambda x: x['score'], reverse=True)
    return [item['path'] for item in relevant[:10]]


def _identify_tech_stack(files_data: Dict) -> List[str]:
    """Identify technology stack from dependencies and files."""
    stack = []
    
    # Check dependencies
    for dep_file, content in files_data['dependencies'].items():
        if dep_file == 'package.json':
            try:
                pkg = json.loads(content)
                deps = {**pkg.get('dependencies', {}), **pkg.get('devDependencies', {})}
                
                if 'next' in deps:
                    stack.append('Next.js')
                if 'react' in deps:
                    stack.append('React')
                if 'vue' in deps:
                    stack.append('Vue')
                if 'typescript' in deps:
                    stack.append('TypeScript')
                if 'tailwindcss' in deps:
                    stack.append('Tailwind CSS')
                if 'express' in deps:
                    stack.append('Express.js')
            except:
                pass
        
        elif dep_file == 'requirements.txt':
            if 'django' in content.lower():
                stack.append('Django')
            if 'fastapi' in content.lower():
                stack.append('FastAPI')
            if 'flask' in content.lower():
                stack.append('Flask')
    
    # Check file extensions
    extensions = set(f['type'] for f in files_data['files'])
    if '.ts' in extensions or '.tsx' in extensions:
        if 'TypeScript' not in stack:
            stack.append('TypeScript')
    if '.py' in extensions:
        stack.append('Python')
    if '.rs' in extensions:
        stack.append('Rust')
    if '.go' in extensions:
        stack.append('Go')
    
    return stack if stack else ['Unknown']


def _find_code_patterns(files_data: Dict) -> List[str]:
    """Identify common code patterns in the project."""
    patterns = []
    
    # Check for common patterns
    all_content = '\n'.join(f['content'] for f in files_data['files'])
    
    if 'useState' in all_content or 'useEffect' in all_content:
        patterns.append('React Hooks')
    if 'async def' in all_content or 'async function' in all_content:
        patterns.append('Async/Await')
    if 'class ' in all_content and 'def __init__' in all_content:
        patterns.append('Object-Oriented (Python)')
    if 'interface ' in all_content or 'type ' in all_content:
        patterns.append('TypeScript Types/Interfaces')
    if 'router.' in all_content or 'app.get' in all_content:
        patterns.append('REST API Routes')
    
    return patterns if patterns else ['Standard patterns']


def _assess_complexity(files_data: Dict, task_description: str, relevant_files: List[str]) -> str:
    """Assess task complexity."""
    # Simple heuristic based on:
    # - Number of relevant files
    # - Task keywords
    # - Project size
    
    task_lower = task_description.lower()
    
    # High complexity indicators
    if any(word in task_lower for word in ['refactor', 'migrate', 'architecture', 'redesign']):
        return 'High'
    
    # Low complexity indicators
    if any(word in task_lower for word in ['fix', 'update', 'add button', 'change color']):
        if len(relevant_files) <= 3:
            return 'Low'
    
    # Medium by default
    return 'Medium'


def _generate_insights(files_data: Dict, task_description: str) -> List[str]:
    """Generate key insights about the project."""
    insights = []
    
    total_files = files_data['metadata']['total_files']
    project_type = files_data['metadata']['project_type']
    
    insights.append(f"Project contains {total_files} relevant files")
    insights.append(f"Detected as {project_type} project")
    
    # Check for testing files
    test_files = [f for f in files_data['files'] if 'test' in f['path'].lower()]
    if test_files:
        insights.append(f"Found {len(test_files)} test files - testing infrastructure exists")
    else:
        insights.append("No test files detected - consider adding tests")
    
    # Check for documentation
    doc_files = [f for f in files_data['files'] if f['path'].endswith('.md')]
    if doc_files:
        insights.append(f"Documentation exists ({len(doc_files)} markdown files)")
    
    return insights


# ============================================================================
# PHASE 3: GENERATE EXECUTION PLAN
# ============================================================================

def _generate_plan(
    files_data: Dict,
    analysis: Dict,
    task_description: str,
    show_live: bool
) -> Dict:
    """
    Phase 3: Generate detailed execution plan with JSON subtasks structure.
    
    Returns:
        {
            'summary': str,
            'approach': str,
            'tasks': [  # Changed from 'steps' to 'tasks' for JSON format
                {
                    'id': int,
                    'title': str,
                    'dependencies': List[str],  # Task titles this depends on
                    'complexity': str,  # Low/Medium/High
                    'description': str,
                    'files_to_modify': List[str],
                    'files_to_create': List[str],
                    'commands': List[str],
                    'test_strategy': str,
                    'subtasks': [
                        {'title': str, 'status': 'pending'},
                        ...
                    ]
                }
            ],
            'risks': List[str],
            'considerations': List[str]
        }
    """
    
    plan = {
        'summary': '',
        'approach': '',
        'tasks': [],  # Changed from 'steps'
        'risks': [],
        'considerations': []
    }
    
    # Generate summary
    plan['summary'] = f"Implementation plan for: {task_description}"
    
    # Determine approach
    plan['approach'] = _determine_approach(files_data, analysis, task_description)
    
    # Generate implementation tasks with subtasks
    plan['tasks'] = _generate_implementation_tasks(
        files_data,
        analysis,
        task_description
    )
    
    # Identify risks
    plan['risks'] = _identify_risks(files_data, analysis, task_description)
    
    # Generate considerations
    plan['considerations'] = _generate_considerations(files_data, analysis)
    
    if show_live:
        total_subtasks = sum(len(task['subtasks']) for task in plan['tasks'])
        console.print(f"  [green]✓ Generated {len(plan['tasks'])} tasks with {total_subtasks} subtasks[/green]")
        console.print(f"  [yellow]Identified {len(plan['risks'])} potential risks[/yellow]")
    
    return plan


def _determine_approach(files_data: Dict, analysis: Dict, task_description: str) -> str:
    """Determine the recommended approach for the task."""
    task_lower = task_description.lower()
    complexity = analysis['complexity_assessment']
    
    if 'add' in task_lower and 'feature' in task_lower:
        return (
            f"Incremental Feature Addition ({complexity} Complexity)\n"
            f"│ Create new components/modules without disrupting existing code\n"
            f"│ Follow existing project patterns: {', '.join(analysis['existing_patterns'][:3])}\n"
            f"└ Test incrementally as each component is added"
        )
    
    elif 'fix' in task_lower or 'bug' in task_lower:
        return (
            f"Bug Fix Approach ({complexity} Complexity)\n"
            f"│ Identify root cause in relevant files\n"
            f"│ Apply minimal changes to fix the issue\n"
            f"│ Add regression tests to prevent recurrence\n"
            f"└ Verify fix doesn't introduce new issues"
        )
    
    elif 'refactor' in task_lower:
        return (
            f"Safe Refactoring Strategy ({complexity} Complexity)\n"
            f"│ Create comprehensive tests before refactoring\n"
            f"│ Refactor in small, testable increments\n"
            f"│ Maintain backward compatibility where possible\n"
            f"└ Validate after each step"
        )
    
    else:
        return (
            f"Standard Implementation ({complexity} Complexity)\n"
            f"│ Follow {analysis['architecture']} patterns\n"
            f"│ Modify {len(analysis['relevant_files'])} relevant files\n"
            f"│ Test thoroughly after implementation\n"
            f"└ Document changes"
        )


def _generate_implementation_tasks(
    files_data: Dict,
    analysis: Dict,
    task_description: str
) -> List[Dict]:
    """Generate detailed implementation tasks with subtasks in JSON-friendly format."""
    tasks = []
    
    task_lower = task_description.lower()
    complexity = analysis['complexity_assessment']
    
    # Task 1: Analysis & Setup
    task_1 = {
        'id': 1,
        'title': 'Project Analysis & Environment Setup',
        'dependencies': [],
        'complexity': 'Low',
        'description': f'Review project structure, analyze {len(analysis["relevant_files"])} relevant files, and ensure development environment is properly configured.',
        'files_to_modify': [],
        'files_to_create': [],
        'commands': _get_setup_commands(files_data),
        'test_strategy': 'Verify all files are accessible and dependencies are installed.',
        'subtasks': [
            {'title': 'Read and understand project structure', 'status': 'pending'},
            {'title': 'Analyze relevant files for current implementation', 'status': 'pending'},
            {'title': 'Review existing patterns and conventions', 'status': 'pending'},
            {'title': 'Verify development environment setup', 'status': 'pending'}
        ]
    }
    tasks.append(task_1)
    
    # Task 2: Core Implementation (varies based on task type)
    if 'add' in task_lower or 'create' in task_lower or 'implement' in task_lower:
        task_2 = {
            'id': 2,
            'title': 'Core Feature Implementation',
            'dependencies': ['Project Analysis & Environment Setup'],
            'complexity': complexity,
            'description': _generate_implementation_description(task_description, analysis),
            'files_to_modify': analysis['relevant_files'][:5],
            'files_to_create': _suggest_new_files(task_description, files_data),
            'commands': [],
            'test_strategy': 'Test each component as it is implemented. Ensure no breaking changes to existing functionality.',
            'subtasks': _generate_feature_subtasks(task_description, analysis)
        }
        tasks.append(task_2)
        
    elif 'fix' in task_lower or 'bug' in task_lower or 'debug' in task_lower:
        task_2 = {
            'id': 2,
            'title': 'Bug Investigation & Root Cause Analysis',
            'dependencies': ['Project Analysis & Environment Setup'],
            'complexity': 'Medium',
            'description': 'Identify the root cause of the bug by analyzing relevant code, checking logs, and reproducing the issue.',
            'files_to_modify': [],
            'files_to_create': [],
            'commands': [],
            'test_strategy': 'Reproduce the bug consistently before attempting fixes.',
            'subtasks': [
                {'title': 'Reproduce the bug in development environment', 'status': 'pending'},
                {'title': 'Analyze stack traces and error logs', 'status': 'pending'},
                {'title': 'Identify affected code sections', 'status': 'pending'},
                {'title': 'Determine root cause of the issue', 'status': 'pending'}
            ]
        }
        tasks.append(task_2)
        
        task_3 = {
            'id': 3,
            'title': 'Apply Bug Fix',
            'dependencies': ['Bug Investigation & Root Cause Analysis'],
            'complexity': 'Low',
            'description': 'Apply minimal changes to fix the identified bug without introducing new issues.',
            'files_to_modify': analysis['relevant_files'][:3],
            'files_to_create': [],
            'commands': [],
            'test_strategy': 'Verify fix resolves the issue and add regression test.',
            'subtasks': [
                {'title': 'Implement the fix using patch_file tool', 'status': 'pending'},
                {'title': 'Test that the bug is resolved', 'status': 'pending'},
                {'title': 'Verify no new issues introduced', 'status': 'pending'},
                {'title': 'Add regression test to prevent future occurrences', 'status': 'pending'}
            ]
        }
        tasks.append(task_3)
        
    elif 'refactor' in task_lower or 'reorganize' in task_lower:
        task_2 = {
            'id': 2,
            'title': 'Refactoring Strategy & Test Coverage',
            'dependencies': ['Project Analysis & Environment Setup'],
            'complexity': 'High',
            'description': 'Create comprehensive tests before refactoring to ensure behavior remains unchanged.',
            'files_to_modify': [],
            'files_to_create': _suggest_test_files(files_data),
            'commands': _get_test_commands(files_data),
            'test_strategy': 'Establish baseline test coverage before any refactoring begins.',
            'subtasks': [
                {'title': 'Identify code sections to refactor', 'status': 'pending'},
                {'title': 'Create or update tests for current behavior', 'status': 'pending'},
                {'title': 'Run tests to establish baseline', 'status': 'pending'},
                {'title': 'Document refactoring plan', 'status': 'pending'}
            ]
        }
        tasks.append(task_2)
        
        task_3 = {
            'id': 3,
            'title': 'Execute Refactoring',
            'dependencies': ['Refactoring Strategy & Test Coverage'],
            'complexity': 'High',
            'description': 'Refactor code in small, testable increments while maintaining passing tests.',
            'files_to_modify': analysis['relevant_files'],
            'files_to_create': [],
            'commands': _get_test_commands(files_data),
            'test_strategy': 'Run tests after each refactoring step. All tests must pass before proceeding.',
            'subtasks': [
                {'title': 'Refactor first code section', 'status': 'pending'},
                {'title': 'Run tests and verify they pass', 'status': 'pending'},
                {'title': 'Refactor next code section', 'status': 'pending'},
                {'title': 'Continue incremental refactoring with testing', 'status': 'pending'}
            ]
        }
        tasks.append(task_3)
    
    else:
        # Generic implementation
        task_2 = {
            'id': 2,
            'title': 'Implementation',
            'dependencies': ['Project Analysis & Environment Setup'],
            'complexity': complexity,
            'description': f'Implement the requested changes: {task_description}',
            'files_to_modify': analysis['relevant_files'][:5],
            'files_to_create': [],
            'commands': [],
            'test_strategy': 'Test functionality after implementation.',
            'subtasks': [
                {'title': 'Implement core functionality', 'status': 'pending'},
                {'title': 'Update related components', 'status': 'pending'},
                {'title': 'Ensure integration with existing code', 'status': 'pending'},
                {'title': 'Handle edge cases', 'status': 'pending'}
            ]
        }
        tasks.append(task_2)
    
    # Task N-1: Testing & Validation
    task_test = {
        'id': len(tasks) + 1,
        'title': 'Comprehensive Testing & Validation',
        'dependencies': [tasks[-1]['title']],  # Depends on last implementation task
        'complexity': 'Medium',
        'description': 'Run all tests, perform integration testing, and validate that the implementation meets requirements.',
        'files_to_modify': [],
        'files_to_create': _suggest_test_files(files_data),
        'commands': _get_test_commands(files_data),
        'test_strategy': 'All tests must pass. Manual testing should verify user-facing functionality.',
        'subtasks': [
            {'title': 'Run unit tests', 'status': 'pending'},
            {'title': 'Run integration tests', 'status': 'pending'},
            {'title': 'Perform manual testing of key workflows', 'status': 'pending'},
            {'title': 'Test edge cases and error handling', 'status': 'pending'},
            {'title': 'Verify performance is acceptable', 'status': 'pending'}
        ]
    }
    tasks.append(task_test)
    
    # Task N: Documentation & Cleanup
    task_final = {
        'id': len(tasks) + 1,
        'title': 'Documentation & Code Cleanup',
        'dependencies': ['Comprehensive Testing & Validation'],
        'complexity': 'Low',
        'description': 'Update documentation, add code comments, and clean up any temporary code or files.',
        'files_to_modify': ['README.md', 'CHANGELOG.md'],
        'files_to_create': [],
        'commands': [],
        'test_strategy': 'Verify documentation is accurate and helpful.',
        'subtasks': [
            {'title': 'Add inline code comments for complex logic', 'status': 'pending'},
            {'title': 'Update README with new features or changes', 'status': 'pending'},
            {'title': 'Update CHANGELOG with version notes', 'status': 'pending'},
            {'title': 'Remove any debug code or temporary files', 'status': 'pending'},
            {'title': 'Run linter and format code', 'status': 'pending'}
        ]
    }
    tasks.append(task_final)
    
    return tasks


def _generate_implementation_description(task_description: str, analysis: Dict) -> str:
    """Generate detailed description for implementation task."""
    patterns = ', '.join(analysis['existing_patterns'][:3]) if analysis['existing_patterns'] else 'existing patterns'
    return f"Implement {task_description} following {patterns}. Ensure compatibility with {analysis['architecture']} architecture."


def _suggest_new_files(task_description: str, files_data: Dict) -> List[str]:
    """Suggest files that may need to be created."""
    project_type = files_data['metadata']['project_type']
    task_lower = task_description.lower()
    
    suggestions = []
    
    if 'auth' in task_lower or 'login' in task_lower:
        if project_type == 'nextjs':
            suggestions = ['app/api/auth/[...nextauth]/route.ts', 'components/LoginForm.tsx', 'lib/auth.ts']
        elif 'python' in project_type:
            suggestions = ['auth.py', 'middleware/auth_middleware.py']
    
    elif 'api' in task_lower or 'endpoint' in task_lower:
        if project_type == 'nextjs':
            suggestions = ['app/api/new-endpoint/route.ts']
        elif 'python' in project_type:
            suggestions = ['routers/new_router.py']
    
    elif 'component' in task_lower:
        if project_type in ['nextjs', 'react', 'vue']:
            suggestions = ['components/NewComponent.tsx']
    
    return suggestions


def _suggest_test_files(files_data: Dict) -> List[str]:
    """Suggest test files that may need to be created."""
    project_type = files_data['metadata']['project_type']
    
    if project_type in ['nextjs', 'react']:
        return ['__tests__/feature.test.tsx', '__tests__/integration.test.tsx']
    elif 'python' in project_type:
        return ['tests/test_feature.py', 'tests/test_integration.py']
    elif project_type == 'rust':
        return ['tests/integration_tests.rs']
    
    return ['tests/']


def _get_setup_commands(files_data: Dict) -> List[str]:
    """Get setup/verification commands."""
    project_type = files_data['metadata']['project_type']
    
    if project_type in ['nextjs', 'react', 'vue']:
        return ['npm install', 'npm run dev']
    elif 'python' in project_type:
        return ['pip install -r requirements.txt', 'python -m pytest --version']
    elif project_type == 'rust':
        return ['cargo check']
    
    return []


def _generate_feature_subtasks(task_description: str, analysis: Dict) -> List[Dict]:
    """Generate specific subtasks based on feature being added."""
    task_lower = task_description.lower()
    
    # Authentication feature
    if 'auth' in task_lower or 'login' in task_lower:
        return [
            {'title': 'Create authentication API endpoints', 'status': 'pending'},
            {'title': 'Implement login/logout functionality', 'status': 'pending'},
            {'title': 'Add authentication middleware', 'status': 'pending'},
            {'title': 'Create login UI components', 'status': 'pending'},
            {'title': 'Add session management', 'status': 'pending'},
            {'title': 'Implement password hashing and validation', 'status': 'pending'}
        ]
    
    # UI component
    elif 'component' in task_lower or 'ui' in task_lower or 'interface' in task_lower:
        return [
            {'title': 'Design component structure and props', 'status': 'pending'},
            {'title': 'Implement base component logic', 'status': 'pending'},
            {'title': 'Add styling and responsive design', 'status': 'pending'},
            {'title': 'Handle user interactions and events', 'status': 'pending'},
            {'title': 'Add accessibility features', 'status': 'pending'},
            {'title': 'Integrate component into application', 'status': 'pending'}
        ]
    
    # API endpoint
    elif 'api' in task_lower or 'endpoint' in task_lower:
        return [
            {'title': 'Define API route and HTTP methods', 'status': 'pending'},
            {'title': 'Implement request validation', 'status': 'pending'},
            {'title': 'Add business logic for endpoint', 'status': 'pending'},
            {'title': 'Implement error handling', 'status': 'pending'},
            {'title': 'Add API documentation', 'status': 'pending'},
            {'title': 'Test API with various inputs', 'status': 'pending'}
        ]
    
    # Database/data feature
    elif 'database' in task_lower or 'model' in task_lower or 'data' in task_lower:
        return [
            {'title': 'Design database schema', 'status': 'pending'},
            {'title': 'Create migration scripts', 'status': 'pending'},
            {'title': 'Implement data models', 'status': 'pending'},
            {'title': 'Add CRUD operations', 'status': 'pending'},
            {'title': 'Implement data validation', 'status': 'pending'},
            {'title': 'Add database indexes for performance', 'status': 'pending'}
        ]
    
    # Generic feature
    else:
        return [
            {'title': 'Implement core feature logic', 'status': 'pending'},
            {'title': 'Add supporting utilities and helpers', 'status': 'pending'},
            {'title': 'Integrate with existing systems', 'status': 'pending'},
            {'title': 'Handle error cases', 'status': 'pending'},
            {'title': 'Add logging and monitoring', 'status': 'pending'},
            {'title': 'Optimize for performance', 'status': 'pending'}
        ]


def _get_test_commands(files_data: Dict) -> List[str]:
    """Get appropriate test commands for the project."""
    project_type = files_data['metadata']['project_type']
    
    commands = []
    
    if project_type in ['nextjs', 'react', 'vue']:
        commands = ['npm test', 'npm run build']
    elif 'python' in project_type:
        commands = ['pytest', 'python -m pytest']
    elif project_type == 'rust':
        commands = ['cargo test', 'cargo build']
    elif project_type == 'go':
        commands = ['go test ./...', 'go build']
    
    return commands if commands else ['Run project-specific tests']


def _identify_risks(files_data: Dict, analysis: Dict, task_description: str) -> List[str]:
    """Identify potential risks."""
    risks = []
    
    task_lower = task_description.lower()
    
    if 'database' in task_lower or 'migration' in task_lower:
        risks.append('Database schema changes may require migration scripts')
        risks.append('Ensure database backups before applying changes')
    
    if 'auth' in task_lower or 'security' in task_lower:
        risks.append('Security-sensitive changes require thorough review')
        risks.append('Test authentication flows extensively')
    
    if 'api' in task_lower:
        risks.append('API changes may break existing clients')
        risks.append('Consider versioning for breaking changes')
    
    if analysis['complexity_assessment'] == 'High':
        risks.append('High complexity task - break into smaller subtasks')
        risks.append('Plan for extensive testing and validation')
    
    # Check for missing tests
    test_files = [f for f in files_data['files'] if 'test' in f['path'].lower()]
    if not test_files:
        risks.append('No existing tests detected - changes may introduce regressions')
    
    return risks if risks else ['Standard implementation risks - test thoroughly']


def _generate_considerations(files_data: Dict, analysis: Dict) -> List[str]:
    """Generate implementation considerations."""
    considerations = []
    
    # Tech stack considerations
    stack = analysis['technology_stack']
    if 'TypeScript' in stack:
        considerations.append('Maintain type safety throughout implementation')
    if 'React' in stack:
        considerations.append('Follow React best practices and hooks guidelines')
    if 'Next.js' in stack:
        considerations.append('Consider server vs client components in App Router')
    
    # Architecture considerations
    if 'API' in analysis['architecture']:
        considerations.append('Ensure API endpoints follow RESTful conventions')
    
    # Pattern considerations
    if 'React Hooks' in analysis['existing_patterns']:
        considerations.append('Use React Hooks consistently with existing patterns')
    
    considerations.append('Follow existing code style and naming conventions')
    considerations.append('Update tests to cover new functionality')
    
    return considerations


# ============================================================================
# OUTPUT FORMATTING & SAVING
# ============================================================================

def _format_detailed_plan(
    task_description: str,
    working_dir: str,
    files_data: Dict,
    analysis: Dict,
    plan: Dict
) -> str:
    """Format the detailed execution plan as JSON."""
    
    # Create the JSON structure
    plan_json = {
        'project_info': {
            'task': task_description,
            'working_directory': working_dir,
            'generated_at': datetime.now().isoformat(),
            'project_type': files_data['metadata']['project_type'],
            'files_analyzed': files_data['metadata']['total_files'],
            'total_size_kb': round(files_data['metadata']['total_size'] / 1024, 2)
        },
        'analysis': {
            'architecture': analysis['architecture'],
            'technology_stack': analysis['technology_stack'],
            'complexity': analysis['complexity_assessment'],
            'relevant_files': [
                os.path.relpath(f, working_dir) if os.path.isabs(f) else f 
                for f in analysis['relevant_files'][:10]
            ],
            'key_insights': analysis['key_insights'],
            'existing_patterns': analysis['existing_patterns']
        },
        'approach': plan['approach'],
        'tasks': plan['tasks'],  # This now has the full task structure with subtasks
        'risks': plan['risks'],
        'considerations': plan['considerations'],
        'metadata': {
            'total_tasks': len(plan['tasks']),
            'total_subtasks': sum(len(task['subtasks']) for task in plan['tasks']),
            'estimated_complexity': analysis['complexity_assessment']
        }
    }
    
    # Pretty print JSON
    return json.dumps(plan_json, indent=2, ensure_ascii=False)


def _save_plan_to_file(task_description: str, plan_content: str, working_dir: str) -> Optional[str]:
    """Save plan to plans/ directory as JSON file."""
    try:
        # Create plans directory
        plans_dir = os.path.join(working_dir, 'plans')
        os.makedirs(plans_dir, exist_ok=True)
        
        # Sanitize task description for filename
        safe_title = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in task_description)
        safe_title = safe_title.strip().replace(' ', '_')[:50]  # Limit length
        
        # Add timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{safe_title}_{timestamp}.json"  # Changed to .json
        
        # Full path
        file_path = os.path.join(plans_dir, filename)
        
        # Write plan as JSON
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(plan_content)
        
        return file_path
    
    except Exception as e:
        console.print(f"[yellow]Warning: Could not save plan to file: {e}[/yellow]")
        return None


# ============================================================================
# SCHEMA FOR AI AGENT INTEGRATION
# ============================================================================

from google.genai import types

schema_plan_project = types.FunctionDeclaration(
    name="plan_project",
    description="""Intelligent project planning tool that performs comprehensive codebase analysis:

Phase 1 - READ: Scans and reads relevant project files intelligently
Phase 2 - THINK: Analyzes architecture, patterns, and complexity  
Phase 3 - PLAN: Generates detailed step-by-step execution plan

The tool automatically:
- Detects project type (Next.js, React, Python, etc.)
- Identifies relevant files based on task description
- Analyzes codebase structure and patterns
- Assesses implementation complexity
- Generates actionable steps with file modifications
- Identifies risks and considerations
- Saves plan to plans/ directory

Perfect for: feature additions, bug fixes, refactoring, architecture changes.
The plan is saved and the agent can immediately start executing the steps.""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Root directory of the project to analyze (default: current directory)",
            ),
            "task_description": types.Schema(
                type=types.Type.STRING,
                description="Clear description of what you want to accomplish (e.g., 'Add user authentication with JWT', 'Fix payment processing bug', 'Refactor API routes')",
            ),
            "file_patterns": types.Schema(
                type=types.Type.ARRAY,
                description="Optional: List of glob patterns to include (e.g., ['*.py', '*.js']). Auto-detected based on project type if not provided.",
                items=types.Schema(type=types.Type.STRING),
            ),
            "max_files": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of files to analyze (default: 50). Prevents overwhelming analysis.",
            ),
            "include_dependencies": types.Schema(
                type=types.Type.BOOLEAN,
                description="Whether to read dependency files like package.json, requirements.txt (default: true)",
            ),
        },
        required=["task_description"],
    ),
)