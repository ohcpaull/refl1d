"""
.. sidebar:: On this Page
    
        * :class:`Cheby volume fraction <refl1d.cheby.ChebyVF>`
        * :class:`Free form cheby <refl1d.cheby.FreeformCheby>`
        * :func:`Layer thickness <refl1d.polymer.layer_thickness>`
        * :func:`Smear <refl1d.polymer.smear>`

Two variants:

    #. direct: use the chebyshev polynomial coefficients directly
    #. interp: interpolate through control points to set the coefficients.

Control points are located at fixed z_k::

    >>> z_k = L * (cos( pi*(k-0.5)/N )+1)/2 for k in 1..N

where L is the thickness of the layer.

Interpolation as an O(N log N) cost to calculation of the profile for N
coefficients.  This is in addition to the O (N S) cost of the direct
profile for S microslabs.


"""
#TODO: clipping volume fraction to [0,1] distorts parameter space
# Option 0: clip to [0,1]
# - Bayesian analysis: parameter values outside the domain will be equally
#   probable out to infinity
# - Newton methods: the fit space is flat outside the domain, which leads
#   to a degenerate hessian.
# - Direct methods: won't fail, but will be subject to random walk
#   performance outside the domain.
# - trivial to implement!
# Option 1: compress (-inf,0.001] and [0.999,inf) into (0,0.001], [0.999,1)
# - won't address any of the problems of clipping
# Option 2: have chisq return inf for points outside the domain
# - Bayesian analysis: correctly assigns probability zero
# - Newton methods: degenerate Hessian outside domain
# - Direct methods: random walk outside domain
# - easy to implement
# Option 3: clip outside domain but add penalty based on amount of clipping
#   A profile based on clipping may have lower chisq than any profile that
#   can be described by a valid model (e.g., by having a sharper transition
#   than would be allowed by the model), leading to a minimum outside D.
#   Adding a penalty constant outside D would help, but there is no constant
#   that works everywhere.  We could use a constant greater than the worst
#   chisq seen so far in D, which can guarantee an arbitrarily low P(x) and
#   a global minimum within D, but for Newton methods, the boundary may still
#   have spurious local minima and objective value now depends on history.
#   Linear compression of profile to fit within the domain would avoid
#   unreachable profile shapes (this is just a linear transform on chebyshev
#   coefficients), and the addition of the penalty value would reduce
#   parameter correlations that result from having transformed parameters
#   resulting in identical profiles.  Returning T = ||A(x)|| from render,
#   with A being a transform that brings the profile within [0,1], the
#   objective function can return P'(x) = P(x)/(10*(1+sum(T_i)^4) for all
#   slabs i, or P(x) if no slabs return a penalty value.  So long as T is
#   monotonic with increasing badness, with value of 0 within D, and so long
#   as no values of x outside D can generate models that cannot be
#   expressed for any x within D, then any optimizer should return a valid
#   result at the global minimum.  There may still be local minima outside
#   the boundary, so information that the the value is outside the domain
#   still needs to pass through a local optimizer to the fitting program.
#   This approach could be used to transform a box constrained
#   problem to an unconstrained problem using clipping+penalty on the
#   parameter values and removing the need for constrained Newton optimizers.
# - Bayesian analysis: parameters outside D have incorrect probability, but
#   with a sufficiently large penalty, P(x) ~ 0; if the penalty value is
#   too low, details of the correlations outside D may leak into D.
# - Newton methods: Hessian should point back to domain
# - Direct methods: random walk should be biased toward the domain
# - moderately complicated
import numpy
from numpy import inf, real, imag, exp, pi
from numpy.fft import fft
from mystic import Parameter as Par, IntegerParameter as IntPar
from .model import Layer

#TODO: add left_sld,right_sld to all layers so that fresnel works
#TODO: access left_sld,right_sld so freeform doesn't need left,right
#TODO: restructure to use vector parameters
#TODO: allow the number of layers to be adjusted by the fit
class FreeformCheby(Layer):
    """
    A freeform section of the sample modeled with Chebyshev polynomials.

    sld (rho) and imaginary sld (irho) can be modeled with a separate
    polynomial orders.
    """
    def __init__(self, thickness=0, rho=[], irho=[],
                 name="Cheby", method="interp"):
        self.name = name
        self.method = method
        self.thickness = Par.default(thickness, limits=(0,inf),
                                   name=name+" thickness")
        self.rho,self.irho \
            = [[Par.default(p,name=name+"[%d] %s"%(i,part),limits=limits)
                for i,p in enumerate(v)]
               for v,part,limits in zip((rho, irho),
                                        ('rho', 'irho'),
                                        ((-inf,inf),(-inf,inf)),
                                        )]
    def parameters(self):
        return dict(rho=self.rho,
                    irho=self.irho,
                    thickness=self.thickness)
    def render(self, probe, slabs):
        thickness = self.thickness.value
        Pw,Pz = slabs.microslabs(thickness)
        t = Pz/thickness
        Prho = cheby_profile([p.value for p in self.rho], t, self.method)
        Pirho = cheby_profile([p.value for p in self.irho], t, self.method)
        slabs.extend(rho=[Prho], irho=[Pirho], w=Pw)

class ChebyVF(Layer):
    """
    Material in a solvent

    :Parameters:

        *thickness* : float | Angstrom
            the thickness of the solvent layer
        *interface* : float | Angstrom
            the rms roughness of the solvent surface
        *material* : Material
            the material of interest
        *solvent* : Material
            the solvent or vacuum
        *vf* : [float]
            the control points for volume fraction
        *method* = 'interp' : string | 'direct' or 'interp'
            freeform profile method

    *method* is 'direct' if the *vf* values refer to chebyshev
    polynomial coefficients or 'interp' if *vf* values refer to
    control points located at z_k::

       >>> z_k = L*(cos(pi (k-0.5)/N)+1) + 1)/2  for k=1..N.

    The materials can either use the scattering length density directly,
    such as PDMS = SLD(0.063, 0.00006) or they can use chemical composition
    and material density such as PDMS=Material("C2H6OSi",density=0.965).

    These parameters combine in the following profile formula::

       >>> sld(z) = material.sld * profile(z) + solvent.sld * (1 - profile(z))
    """
    def __init__(self, thickness=0, interface=0,
                 material=None, solvent=None,
                 length=None, vf=None, method="interp"):
        self.thickness = Par.default(thickness, name="solvent thickness")
        self.interface = Par.default(interface, name="solvent interface")
        self.length = Par.default(length, name="length")
        self.solvent = solvent
        self.material = material
        self.vf = [Par.default(p,name="vf[%d]"%i) for i,p in enumerate(vf)]
        self.method = method
        # Constraints:
        #   base_vf in [0,1]
        #   base,length,sigma,thickness,interface>0
        #   base+length+3*sigma <= thickness
    def parameters(self):
        return dict(solvent=self.solvent.parameters(),
                    material=self.material.parameters(),
                    thickness=self.thickness,
                    interface=self.interface,
                    length=self.length,
                    vf=self.vf)
    def render(self, probe, slabs):
        Mr,Mi = self.material.sld(probe)
        Sr,Si = self.solvent.sld(probe)
        M = Mr + 1j*Mi
        S = Sr + 1j*Si
        try: M,S = M[0],S[0]  # Temporary hack
        except: pass

        thickness = self.thickness.value
        Pw,Pz = slabs.microslabs(thickness)
        t = Pz/thickness
        vf = cheby_profile([p.value for p in self.vf], t, self.method)
        vf = numpy.clip(vf,0,1)
        P = M*vf + S*(1-vf)
        Pr, Pi = real(P), imag(P)
        slabs.extend(rho=[Pr], irho=[Pi], w=Pw)

def cheby_profile(control, t, method):
    n = len(control)
    if n == 0:
        return 0*t
    c = numpy.array(control)
    if method == 'interp':
        # DCT calculation of chebyshev coefficients
        #    c_j = 2/N sum_k=1^N f_k cos((2 pi j (k-1) / 2 N)
        # where
        #    f_k = f[cos( (2 pi k - 1) / 2 N]
        w = exp((-0.5j*pi/n)*numpy.arange(n))
        y = numpy.hstack((c[0::2], c[1::2][::-1]))
        c = (2./n) * real(fft(y)*w)

    # Crenshaw recursion from numerical recipes sec. 5.8
    y = 4*t - 2
    d = dd = 0
    for c_j in c[:0:-1]:
        d, dd = y*d + (c_j - dd), d
    return y*(0.5*d) + (0.5*c[0] - dd)