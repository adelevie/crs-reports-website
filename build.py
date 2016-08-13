#!/usr/bin/python3
#
# Build the CRS Reports Archive website by generating its static content.
#
# Assumptions:
#
# * The CRS report metadata and document files are in the 'reports/reports' and 'reports/files' directories.
#   (Thie JSON metadata is the transformed metadata that we published, not what we scraped from CRS.)
#
# Output:
#
# * A static website in ./build.

import sys, os.path, glob, shutil, collections, json, datetime, re
from multiprocessing import Pool

REPORTS_DIR = "reports"
BUILD_DIR = "build"


def load_all_reports():
    # Load all of the reports into memory, because we'll have to scan them all for what topic
    # they are in.
    reports = []
    for fn in glob.glob(os.path.join(REPORTS_DIR, "reports/*.json")):
        # Parse the JSON.
        with open(fn) as f:
            report = json.load(f)

        # Parse the datetimes.
        for version in report["versions"]:
            version["date"] = datetime.datetime.strptime(version["date"], "%Y-%m-%dT%H:%M:%S")
            version["fetched"] = datetime.datetime.strptime(version["fetched"], "%Y-%m-%dT%H:%M:%S.%f")

        reports.append(report)

    return reports


def index_by_topic(reports):
    topic_area_names = { }
    topic_area_reports = collections.defaultdict(lambda : [])
    for report in reports:
        topics = set()
        for version in report["versions"]:
            for topic_id, topic_name in version["topics"]:
                topics.add(topic_id)

                # The textual name of a topic area might change, but the ID is probably persistent.
                # Remember the most recent topic area textual name for each topic ID.
                if topic_id not in topic_area_names or topic_area_names[topic_id][0] < version["date"]:
                    topic_area_names[topic_id] = (version["date"], topic_name)

        for topic in topics:
            topic_area_reports[topic].append(report)

    return [{
               "id": topic_id,
               "title": topic_area_names[topic_id][1],
               "reports": topic_area_reports[topic_id],
           }
           for topic_id
           in sorted(topic_area_names, key = lambda topic_id : topic_area_names[topic_id][1])]


def generate_static_page(fn, context, output_fn=None):
    # Generates a static HTML page by executing the Jinja2 template.
    # Given "index.html", it writes out "build/index.html".

    # Construct the output file name.

    if output_fn is None:
        output_fn = fn
    output_fn = os.path.join(BUILD_DIR, output_fn)

    print(output_fn, "...")

    # Prepare Jinja2's environment.

    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(["templates", "pages"]))

    # Add some filters.

    def format_datetime(value):
        return value.strftime("%B %-d, %Y")
    env.filters['date'] = format_datetime

    def format_summary(text):
        # Some summaries have double-newlines that are probably paragraph breaks.
        # Other newlines are hard linebreaks at the ends of ~60-column lines that
        # we don't care about.
        import CommonMark
        return CommonMark.commonmark(text)
    env.filters['format_summary'] = format_summary

    def intcomma(value):
        return format(value, ",d")
    env.filters['intcomma'] = intcomma

    # Load the template.

    try:
        templ = env.get_template(fn)
    except Exception as e:
        print("Error loading template", fn)
        print(e)
        sys.exit(1)

    # Execute the template.

    try:
        html = templ.render(context)
    except Exception as e:
        print("Error rendering template", fn)
        print(e)
        sys.exit(1)

    # Write the output.

    os.makedirs(os.path.dirname(output_fn), exist_ok=True)
    with open(output_fn, "w") as f:
        f.write(html)


def generate_static_pages(context):
    # Generate a static page for every HTML file in the pages directory.
    for fn in glob.glob("pages/*.html"):
        generate_static_page(os.path.basename(fn), context)


def copy_static_assets():
    # Copy the static assets from the "static" directory to "build/static".

    print("static assets...")

    # Clear the output directory first. (copytree requires that the destination not exist)
    static_dir = os.path.join(BUILD_DIR, "static")
    if os.path.exists(static_dir):
        shutil.rmtree(static_dir)

    # "Copy" the assets. Actually just make hardlinks since we're not going to be
    # modifying the build output, and the source files are under version control anyway.
    shutil.copytree("static", static_dir, copy_function=os.link)

def save_json(obj, fn):
    class Encoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, datetime.datetime):
                 return obj.isoformat()
            return json.JSONEncoder.default(self, obj)

    with open(os.path.join(BUILD_DIR, fn), "w") as f:
        json.dump(obj, f, indent=2, cls=Encoder)


def generate_report_page(report):
    # Sanity check that report numbers won't cause invalid file paths.
    if not re.match(r"^[0-9A-Z-]+$", report["number"]):
        raise Exception("Report has a number that would cause problems for our URL structure.")

    # Get the HTML report for the template. Always use the most recent version.
    html = None
    for format in report['versions'][0]['formats']:
        if format['format'] != 'HTML': continue
        with open(os.path.join("sanitized-html", format['filename'][6:])) as f:
            html = f.read()

    # Generate the report HTML page.
    generate_static_page("report.html", {
        "report": report,
        "html": html,
    }, output_fn="reports/%s.html" % report["number"])

    # Write the metadata file.
    save_json(report, "reports/%s.json" % report["number"])

    # Copy the actual document files into build output.
    for version in report['versions']:
       for format in version['formats']:
           pass


# MAIN


if __name__ == "__main__":
    # Prepare to split the work across processors.
    pool = Pool()

    # Load all of the report metadata.
    reports = load_all_reports()
    by_topic = index_by_topic(reports)

    # Generate static pages.
    generate_static_pages({
        "reports_count": len(reports),
        "first_report_date": reports[-1]['versions'][-1]['date'],
        "last_report_date": reports[0]['versions'][0]['date'],
        "topics": by_topic,
        "recent_reports": reports[0:20],
    })
    for topic in by_topic:
        if os.environ.get("ONLY"): continue # for debugging
        generate_static_page("topic.html", { "topic": topic }, output_fn="topics/%d.html" % topic["id"])

    # Copy static assets (CSS etc.).
    copy_static_assets()

    # Generate report pages.
    for report in reports:
        # For debugging, skip this report if we didn't ask for it.
        # e.g. ONLY=R41360
        if os.environ.get("ONLY") and report["number"] != os.environ.get("ONLY"):
            continue

        #generate_report_page(report)
        # queue the task
        pool.apply_async(generate_report_page, [report])

    # Hard-link the reports/files directory into the build directory.
    if not os.path.exists("build/files"):
        os.symlink("../reports/files", "build/files")

    # Wait for the last processes to be done.
    pool.close()
    pool.join()


