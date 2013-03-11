import subprocess as sub
import gitflow.pivotal as pivotal
import reviewboard.extensions as rb_ext
import gitflow.core as core
import sys

from gitflow.exceptions import GitflowError


def post_review(self, identifier, name, post_new):
    mgr = self.managers[identifier]
    branch = mgr.by_name_prefix(name)

    sys.stdout.write("Walking the Git reflogs to find review request parent (might "
        "take a couple seconds)...\n")
    sys.stdout.flush()

    if not post_new:
        parent = find_last_patch_parent(self.develop_name(), branch.name)
        if not parent:
            print ("Could not find any merges into %s, using full patch." %
                self.develop_name())
    if not parent:
        parent = get_branch_parent(branch.name)

    if not parent:
        raise GitflowError("Could not find parent for branch '%s'!" %
            branch.name)

    story_id = pivotal.get_story_id_from_branch_name(branch.name)
    story = pivotal.get_story(story_id)

    cmd = ['post-review', '--branch', branch.name,
        '--guess-description',
        '--parent', self.develop_name(),
        '--revision-range', '%s:%s' % (parent, branch.name)]

    if post_new:
        # Create a new request.
        cmd += ['--summary', "'%s'" % story['story']['name']]
    else:
        gitflow = core.GitFlow()
        req = rb_ext.get_latest_review_request_for_branch(
            gitflow.get('reviewboard.server'), branch.name)
        if req:
            # Update an existing request.
            cmd += ['-r', str(req['id'])]
        else:
            # Create a new request.
            cmd += ['--summary', "'%s'" % story['story']['name']]

    print "Posting a review using command: %s" % ' '.join(cmd)
    proc = sub.Popen(cmd, stdout=sub.PIPE)
    (out, err) = proc.communicate()
    # Post a comment to the relevant Pivotal Tracker story (to make it easier to
    # track review requests).
    review_url = out.strip().split('\n')[-1]
    if not review_url.startswith('http'):
        print ("Could not determine review URL (probably an error when "
            "posting the review")
        return
    if '-r' in cmd:
        comment = "Review request %s updated."
    else:
        comment = "Review request posted: %s"
    pivotal.add_comment_to_story(story_id, comment % review_url)

core.GitFlow.post_review = post_review


def get_branch_parent(branch_name):
    proc = sub.Popen(
        ['git', 'reflog', 'show', branch_name],
        env={'GIT_PAGER': 'cat'}, stdout=sub.PIPE)
    (out, err) = proc.communicate()
    lines = out.strip().split('\n')
    parent = None
    for line in lines:
        parts = line.split(' ')
        if len(parts) >= 3 and (parts[2]).startswith('branch'):
            parent = parts[0]
    if not parent:
        parent = lines[-1].split(' ')[0]
    return parent


def find_last_patch_parent(develop_name, branch_name):
    proc = sub.Popen(
        ['git', 'reflog', 'show', develop_name],
        env={'GIT_PAGER': 'cat'}, stdout=sub.PIPE)
    (out, err) = proc.communicate()
    lines = out.strip().split('\n')
    lines = [l for l in lines if ("merge %s" % branch_name) in l]
    if len(lines) < 2:
        return None
    else:
        # Get the hash of the second most recent merge.
        return lines[1].split(' ')[0]
