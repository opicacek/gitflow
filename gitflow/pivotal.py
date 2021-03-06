from gitflow.core import GitFlow
import httplib2
import busyflow.pivotal as pt
import string
from colorama import Style
from colorama import init
init()
import sys
import re
import itertools
import json

from gitflow.exceptions import (NotInitialized, GitflowError,
                                ReleaseAlreadyAssigned, IllegalVersionFormat,
                                StatusError, NoSuchBranchError)


PT_V5_ENDPOINT = 'https://www.pivotaltracker.com/services/v5'

_gitflow = GitFlow()

def check_version_format(version):
    matcher = _get_version_matcher()
    if not re.match(matcher + '$', version):
        raise IllegalVersionFormat(matcher)

def _get_version_matcher():
    return _gitflow._safe_get('gitflow.release.versionmatcher')

def _get_token():
    return _gitflow._safe_get('gitflow.pt.token')

def _get_client():
    return pt.PivotalClient(token=_gitflow._safe_get('gitflow.pt.token'))

def _get_project_id():
    return _gitflow._safe_get('gitflow.pt.projectid')

def _iter_current_stories():
    client = _get_client()
    pid = _get_project_id()
    iterations = client.iterations.current(pid)
    if 'iterations' not in iterations:
        return
    for iteration in iterations['iterations']:
        for story in iteration['stories']:
            s = Story.from_dict(story)
            if s.is_feature() or s.is_bug():
                yield s

def _iter_backlog_stories():
    client = _get_client()
    pid = _get_project_id()
    iterations = client.iterations.backlog(pid)
    if 'iterations' not in iterations:
        return
    for iteration in iterations['iterations']:
        for story in iteration['stories']:
            s = Story.from_dict(story)
            if s.is_feature() or s.is_bug():
                yield s

def _iter_stories():
    return itertools.chain(_iter_current_stories(), _iter_backlog_stories())

def list_projects():
    projects = _get_client().projects.all()['projects']
    return [(p['id'], p['name']) for p in projects]

class Story(object):
    def __init__(self, story_id, _skip_story_download=False):
        self._project_id = _get_project_id()
        self._client = _get_client()
        if _skip_story_download:
            return
        payload = self._client.stories.get(self._project_id, story_id)
        self._story = payload['story']

    def get_id(self):
        return self._story['id']

    def get_name(self):
        return self._story['name']

    def get_url(self):
        return self._story['url']

    def get_type(self):
        return self._story['story_type']

    def get_state(self):
        return self._story['current_state']

    def set_state(self, state):
        self._update(current_state=state)

    def get_labels(self):
        return self._story.get('labels', [])

    def add_label(self, label):
        labels = self.get_labels()
        if label in labels:
            return
        labels.append(label)
        self._update(labels=labels)

    def is_labeled(self, label):
        return label in self.get_labels()

    def add_comment(self, comment):
        self._client.stories.add_comment(
            project_id=self._project_id, story_id=self.get_id(), text=comment)

    def start(self):
        self.set_state('started')

    def is_started(self):
        return self.get_state() == 'started'

    def is_unstarted(self):
        return self.get_state() == 'unstarted'

    def set_me_as_owner(self):
        headers = {'X-TrackerToken': _get_token()}

        # Download /me
        endpoint = PT_V5_ENDPOINT + '/me'
        resp, content = httplib2.Http().request(
                endpoint,
                headers=headers)
        if resp.status != 200:
            raise OperationsError('failed to get Pivotal Tracker /me')

        # Decode my ID
        my_id = None
        try:
            my_id = json.loads(content)['id']
        except ValueError:
            raise OperationsError('failed to decode Pivotal Tracker /me response')

        # Set my ID as the owner of this story
        endpoint = '{0}/projects/{1}/stories/{2}' \
                   .format(PT_V5_ENDPOINT, _get_project_id(), self.get_id())
        req_content = json.dumps({'owned_by_id': my_id})
        headers['Content-Type'] = 'application/json'
        rep, rep_content = httplib2.Http().request(
                endpoint,
                body=req_content,
                method='PUT',
                headers=headers)
        if rep.status != 200:
            raise OperationsError('failed to set me as the story owner')

    #+++ Bug- & Feature-specific stuff
    def finish(self):
        assert self.is_feature() or self.is_bug()
        if self.is_finished():
            return
        if not self.is_started():
            raise StatusError('Feature not started: ' + str(self.get_id()))
        self.set_state('finished')

    def is_finished(self):
        assert self.is_feature() or self.is_bug()
        return self.get_state() == 'finished'

    def deliver(self):
        assert self.is_feature() or self.is_bug()
        if self.is_delivered():
            return
        if not self.is_finished():
            raise StatusError('Feature not finished: ' + str(self.get_id()))
        self.set_state('delivered')

    def is_delivered(self):
        assert self.is_feature() or self.is_bug()
        return self.get_state() == 'delivered'

    def is_accepted(self):
        assert self.is_feature() or self.is_bug()
        return self.get_state() == 'accepted'

    def is_rejected(self):
        assert self.is_feature() or self.is_bug()
        return self.get_state() == 'rejected'

    def get_release(self):
        assert self.is_feature() or self.is_bug()
        for label in self.get_labels():
            m = re.match('release-(' + _get_version_matcher() + ')$', label)
            if m:
                return m.groups()[0]

    def assign_to_release(self, release):
        assert self.is_feature() or self.is_bug()
        check_version_format(release)
        if self.get_release():
            raise ReleaseAlreadyAssigned('Story already assigned to a release')
        self.add_label('release-' + release)

    def get_branch(self):
        assert self.is_feature() or self.is_bug()
        mgr = _gitflow.managers['feature']
        try:
            return mgr.by_name_prefix(str(self.get_id()))
        except NoSuchBranchError:
            pass
        try:
            return mgr.by_name_prefix(str(self.get_id()), remote=True)
        except NoSuchBranchError:
            pass
        return None
    #--- Bug- & Feature-specific stuff

    #+++ Feature-specific stuff
    def is_feature(self):
        return self.get_type() == 'feature'

    def get_estimate(self):
        assert self.is_feature()
        return self._story['estimate']

    def is_estimated(self):
        assert self.is_feature()
        return self.get_estimate() != -1
    #--- Feature-specific stuff

    #+++ Bug-specific stuff
    def is_bug(self):
        return self.get_type() == 'bug'
    #--- Bug-specific stuff

    def dump(self, highlight_labels=[]):
        story = self._story
        sys.stdout.write(colorize_string(story['name']))
        labels = []
        for label in story.get('labels', []):
            if label in highlight_labels:
                lblstr = Style.BRIGHT + str(label) + Style.RESET_ALL
            else:
                lblstr = str(label)
            labels.append(lblstr)
        sys.stdout.write(' ' + Style.DIM + ','.join(labels) + Style.RESET_ALL)
        sys.stdout.write(' (' + Style.DIM + str(story['current_state']) +
            Style.RESET_ALL + ')')
        sys.stdout.write('\n')

    def _update(self, **kwargs):
        try:
            self._client.stories.update(project_id=self._project_id,
                                        story_id=self.get_id(),
                                        **kwargs)
        except pt.RequestError, e:
            msg  = e.parsed_body['message']
            msg += '\n\nMake sure that you are allowed to update this story!'
            raise GitflowError(msg)
        # Commit changes into our internal story instance as well.
        self._story.update(kwargs)

    def to_dict(self):
        return self._story

    @classmethod
    def from_dict(cls, story):
        t = type('Story', (cls,), dict(_story=story))
        return t(0, _skip_story_download=True)


class Release(object):
    def __init__(self, version, _skip_story_download=False):
        check_version_format(version)
        self._version = version
        if _skip_story_download:
            return
        self._current_stories = list(_iter_current_stories())

    def __iter__(self):
        for story in self._current_stories:
            if story.is_labeled('release-' + self._version):
                yield story

    def get_version(self):
        assert self._version
        return self._version

    def start(self):
        for story in self.iter_candidates():
            story.assign_to_release(self._version)

    def try_finish(self):
        err = False
        for story in self:
            if not story.is_labeled('qa+'):
                # Allow zero-point stories not to be QA'd since they are
                # by definition refactoring stories.
                if story.is_feature() and story.get_estimate() == 0:
                    continue
                err = True
                print("    Story not QA'd: " + story.get_url())
            if story.is_labeled('point me'):
                err = True
                print("    Story labeled 'point me': " + story.get_url())
        if err:
            raise StatusError("Pivotal Tracker check did not pass, operation canceled.")

    def finish(self):
        print 'Following stories were delivered for client acceptance as of release %s:' \
              % self._version
        for story in self:
            if not story.is_delivered() \
                    and not story.is_accepted() \
                    and not story.is_rejected():
                story.deliver()
            sys.stdout.write('    ')
            story.dump()

    def try_deliver(self):
        self.try_finish()
        err = False
        for story in self:
            if not story.is_accepted():
                err = True
                print('    Story not accepted: ' + story.get_url())
        if err:
            raise StatusError("Pivotal Tracker check did not pass, operation canceled.")

    def prompt_for_confirmation(self):
        answer = raw_input("Do you wish to start the release? [y/N]: ")
        if answer.lower() == 'y':
            return True
        return False

    def iter_candidates(self):
        for story in self._current_stories:
            if (story.is_feature() or story.is_bug()) \
                    and story.is_finished() \
                    and story.get_release() is None:
                yield story

    def dump_stories(self):
        print '%s %s %s %s' % (32 * '-', 'Release', self._version, 33 * '-')
        for story in self:
            story.dump(highlight_labels=['release-' + self._version])
        print 80 * '-' + '\n'

    @classmethod
    def dump_all_releases(cls):
        stories = dict()
        for story in _iter_current_stories():
            if story.is_feature() or story.is_bug():
                release = story.get_release()
                if release:
                    if release in stories:
                        stories[release].append(story)
                    else:
                        stories[release] = [story]
        for release in stories:
            cls.from_dict(release, stories[release]).dump_stories()

    @classmethod
    def from_dict(cls, version, stories):
        t = type('Release', (cls,), dict(_current_stories=stories))
        return t(version, _skip_story_download=True)


def print_story(story, index=None, highlight_labels=[]):
    if index:
        sys.stdout.write(Style.BRIGHT + str(index) + ' : ' + Style.RESET_ALL)
    sys.stdout.write(colorize_string(story['name']))
    labels = []
    for label in story.get('labels', []):
        if label in highlight_labels:
            lblstr = Style.BRIGHT + str(label) + Style.RESET_ALL
        else:
            lblstr = str(label)
        labels.append(lblstr)
    sys.stdout.write(' ' + Style.DIM + ','.join(labels) + Style.RESET_ALL)
    sys.stdout.write(' (' + Style.DIM + str(story['current_state']) +
        Style.RESET_ALL + ')')
    sys.stdout.write('\n')


def filter_stories(stories, states, types=None):
    types = types or ['feature', 'chore', 'bug']
    return [s for s in stories if s['current_state'] in states and
        s['story_type'] in types]


def prompt_user_to_select_story():
    i = 1
    stories = list()
    print Style.DIM + "--------- Stories -----------" + Style.RESET_ALL
    for story in _iter_stories():
        if story.is_feature() and not story.is_estimated():
            continue
        # You do not start a story if its branch is present or it is
        # assigned to a release, unless it is rejected.
        # If it is not rejected, use checkout instead.
        if (story.get_release() is not None \
                or story.get_branch() is not None) \
                and not story.is_rejected():
            continue
        if story.is_started() \
                or story.is_unstarted() \
                or story.is_rejected():
            stories.append(story)
            print_story(story.to_dict(), i)
            i += 1
    print Style.DIM + "-----------------------------" + Style.RESET_ALL
    print

    if len(stories) == 0:
        raise SystemExit('No story available, aborting!\n\n' \
                'Try "git flow feature list" to find another story' \
                ' to work on.')

    # Prompt user to choose story index.
    print "Select story (or 'q' to quit): "
    index = raw_input()
    if index == "q":
        raise SystemExit("Operation canceled.")

    # We expect a number.
    try:
        index = int(index)
    except ValueError:
        raise GitflowError("Expected a number.")

    if not 0 < index <= len(stories):
        raise GitflowError("Invalid story index: %s." % index)

    # We're expecting input to start from 1, so we have to
    # subtract one here to get the list index.
    story = stories[index - 1]

    if story.is_rejected():
        return [story, None]

    slug = prompt_user_to_select_slug(story)
    return [story, str(story.get_id()) + '/' + slug]


def prompt_user_to_select_slug(story):
    print ("Please choose short story description (will be slugified " +
        "and used as part of the branch name). Don't use whitespace.")
    slug_hint = slugify(get_story_bold_part(story.get_name()))
    print "Story description (default: '%s'):" % slug_hint
    slug = raw_input().strip() or slug_hint
    if any(c in (string.whitespace + '/') for c in slug):
        print ("\n%sERROR%s: The slug must not contain whitespace or '/'.\n" %
            (Style.BRIGHT, Style.RESET_ALL))
        return prompt_user_to_select_slug(story)
    return slug


def get_story_bold_part(story_name):
    """
        >>> get_story_bold_part("*This is the bold part*, this is not.")
        'This is the bold part'
    """
    if not '*' in story_name:
        return story_name
    else:
        return re.sub(r'.*\*(.+)\*.*', r'\1', story_name)


def slugify(story_name, max_length=25):
    """
        >>> slugify(' Just a story name ')
        'just-a-story-name'
        >>> slugify('Just_a_story_name')
        'just_a_story_name'
        >>> slugify('Just_a_story_name_but_a_very_long_one_indeed...')
        'just_a_story_name_but_a_'
        >>> slugify('Just a story name but a very long one indeed...')
        'just-a-story-name-but-a'
    """
    # Inspired by Django.
    value = re.sub('[^\w\s-]', '', story_name).strip().lower()
    value = re.sub('[-\s]+', '-', value)
    if len(value) > max_length:
        value = value[:max_length]
        return value[:(value.rfind('-'))]
    else:
        return value


def get_iterations():
    token = _gitflow.get('gitflow.pt.token')
    project_id = _gitflow.get('gitflow.pt.projectid')
    client = pt.PivotalClient(token=token)
    current = client.iterations.current(project_id)
    backlog = client.iterations.backlog(project_id)
    return [current, backlog]


def update_story(story_id, **kwargs):
    token = _gitflow.get('gitflow.pt.token')
    project_id = _gitflow.get('gitflow.pt.projectid')
    client = pt.PivotalClient(token=token)
    try:
        client.stories.update(
            project_id=project_id, story_id=story_id, **kwargs)
    except pt.RequestError, e:
        msg  = e.parsed_body['message']
        msg += '\n\nMake sure that you are allowed to update this story!'
        raise GitflowError(msg)


def add_comment_to_story(story_id, msg):
    token = _gitflow.get('gitflow.pt.token')
    project_id = _gitflow.get('gitflow.pt.projectid')
    client = pt.PivotalClient(token=token)
    client.stories.add_comment(
        project_id=project_id, story_id=story_id, text =msg)


def get_story(story_id):
    token = _gitflow.get('gitflow.pt.token')
    project_id = _gitflow.get('gitflow.pt.projectid')
    client = pt.PivotalClient(token=token)
    return client.stories.get(project_id=project_id, story_id=story_id)


def finish_story(story_id):
    story = get_story(story_id)
    update_story(story_id, current_state='finished')


def colorize_string(string):
    """ Use ANSI color codes to emulate a simple subset of textile formatting.
        We're supporting '*' (bold).

        Note: Requires 'termcolor' Python module. If not installed, this
        function just returns the string passed in.
    """
    # Remove escaped parts.
    tmp = re.sub('==.*==', '', string)
    bolds = re.findall('\*.+\*', tmp)
    for bold in bolds:
        stripped_asterisks = (Style.BRIGHT + bold.replace('*', '') +
            Style.RESET_ALL)
        tmp = tmp.replace(bold, stripped_asterisks)
    return tmp


def get_story_id_from_branch_name(identifier, branch_name):
    if branch_name.startswith(identifier):
        name = branch_name[len(identifier)+1:]
    else:
        name = branch_name
    match = re.match('^([0-9]+)[/-]?.*$', name)
    if not match:
        raise GitflowError('Weird branch name format: %s' % branch_name)
    return match.groups()[0]


def show_release_summary(gitflow):
    current = get_iterations()[0]
    current_stories = [
        story for i in current['iterations'] for story in i['stories']]

    print Style.BRIGHT + 'Releasable stories:' + Style.RESET_ALL
    candidate_chores = filter_stories(
        current_stories, ['started'], types=['chore'])
    releasable_chores = [chore for chore in candidate_chores
        if 'reviewed' in chore.get('labels', [])]
    candidate_stories = filter_stories(
        current_stories, ['finished'], types=['bug', 'feature'])
    releasable_stories = [s for s in candidate_stories
        if set(['reviewed', 'qa+']).issubset(set(s.get('labels', [])))]
    for item in releasable_stories + releasable_chores:
        sys.stdout.write(' > ')
        print_story(item)

    print Style.BRIGHT + 'Unreleasable stories:' + Style.RESET_ALL
    unreleasable = [item for item in current_stories
        if not item in releasable_chores and not item in releasable_stories]
    for item in unreleasable:
        sys.stdout.write(' < ')
        print_story(item, highlight_labels=['qa+', 'reviewed'])

    #TODO(Tom): Better release summary based on what's already been merged...
    #lines = gitflow.git.log(
    #    '--show-notes=workflow', '--oneline', '--format="%H %N"').split('\n')
    #finished_stories = []
    #for line in lines:
    #    parts = line.split(' ')
    #    if len(parts) == 3:
    #        (hash, op, story_id) = parts
    #        if op == 'finish':
    #            finished_stories.append((hash, story_id))

    #lines = gitflow.git.log('--oneline', '--format="%h %s"').split('\n')
    ##merges = [l for l in lines if l.split(' ')[1].lower() == 'merge']
    #merged_branches = []
    #for line in lines:
    #    match = re.match(
    #        "(.+)\ Merge\ branch\ '(.+)'\ into %s" % gitflow.develop_name(),
    #        line)
    #    if match:
    #        (hash, branch) = match.groups()
    #        if not branch in merged_branches:
    #            merged_branches.append(branch)
    #print merged_branches
    #releasable = releasable_stories + releasable_chores
    #for story in releasable:
    #    stories_merged_before_this_story = \
    #        set([get_story_id_from_branch_name(s) for s in \
    #            merged_branches[merged_branches.index(story) + 1 : ]])
    #    blockers = stories_merged_before_this_story.intersection(
    #        [s['id'] for s in unreleasable])
    #    if blockers:
    #        print "Story '%s' is blocked by stories: %s" % (story, blockers)
    #import ipdb; ipdb.set_tracce()
